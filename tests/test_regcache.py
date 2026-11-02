"""tools/regcache.py: guest registers kept in C locals, the context brought up to date where
something else reads it. Small hand-written bodies, as the lifter marks them."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import regcache  # noqa: E402
from lift import mark_syncs  # noqa: E402


def run(stmts, careful=False):
    """stmts: [(pc, C statement, falls through)] as the lifter makes them (unmarked)."""
    decl, out = regcache.apply([(pc, mark_syncs(s), f) for pc, s, f in stmts], set(), careful)
    return decl, [s for _pc, s in out]


def stores(s):
    return set(re.findall(r"c->x\[(\d+)\] = x_\d+", s)) | ({'sp'} if 'c->sp = x_sp' in s else set())


def loads(s):
    return set(re.findall(r"x_(\d+) = c->x\[\d+\]", s))


CALL = "X(30) = 0x7100001008ull; f_7100002000(c);"


class RegcacheTests(unittest.TestCase):
    def test_locals_replace_the_context(self):
        decl, out = run([(0, "X(1) = X(0) + 0x1ull;", True), (4, "SP = SP - 0x10ull;", True), (8, "return;", False)])
        self.assertIn("u64 x_0 = c->x[0], x_1 = c->x[1];", decl)
        self.assertIn("u64 x_sp = c->sp;", decl)
        self.assertEqual(out[0], "x_1 = x_0 + 0x1ull;")
        self.assertNotIn("X(", "".join(out))
        self.assertEqual(stores(out[2]), {'1', 'sp'})   # what changed, written back before returning

    def test_call_spills_dirty_caller_saved_and_reloads_them(self):
        _decl, out = run([(0, "X(0) = X(19);", True), (4, "X(20) = 0x5ull;", True), (8, CALL, True),
                          (12, "X(2) = X(0) + X(20);", True), (16, "return;", False)])
        # x0 and the link register changed and the callee reads them; x20 (callee-saved) stays put
        self.assertEqual(stores(out[2]), {'0', '30'})
        self.assertTrue({'0', '2', '30'} <= loads(out[2]))
        self.assertNotIn('20', loads(out[2]))
        self.assertEqual(stores(out[4]), {'2', '20'})   # (x0 and x30 are as the context has them)

    def test_frame_pointer_and_sp_reach_the_callee(self):
        _decl, out = run([(0, "SP = SP - 0x20ull;", True), (4, "X(29) = SP + 0x10ull;", True), (8, CALL, True), (12, "return;", False)])
        self.assertEqual(stores(out[2]), {'29', '30', 'sp'})

    def test_branches_merge_what_may_be_dirty(self):
        _decl, out = run([(0, "if (X(0) == 0) goto L_c;", True), (4, "X(1) = 0x1ull;", True),
                          (8, "X(2) = 0x2ull;", True), (12, CALL, True), (16, "return;", False)])
        self.assertEqual(stores(out[3]), {'1', '2', '30'})   # x1 written on the path that falls through

    def test_careful_functions_spill_callee_saved_too(self):
        _decl, out = run([(0, "X(19) = 0x1ull;", True), (4, CALL, True), (8, "return;", False)], careful=True)
        self.assertEqual(stores(out[1]), {'19', '30'})

    def test_landing_pad_reads_everything_back(self):
        pad = ("{ u64 sp_ = SP; int lp_ = 0; __try { " + CALL + " } __except (rt_eh_filter(GetExceptionInformation(), sp_, "
               "0x7100001010ull)) { lp_ = 1; } if (lp_) goto L_10; }")
        _decl, out = run([(0, "X(19) = X(0);", True), (4, pad, True), (8, "return;", False), (0x10, "X(0) = X(19);", True),
                          (0x14, "return;", False)], careful=True)
        self.assertIn("if (lp_) { x_0 = c->x[0]; x_19 = c->x[19]; x_30 = c->x[30]; x_sp = c->sp;", out[1])
        self.assertIn("sp_ = x_sp", out[1])

    def test_exits_inside_conditions_are_braced(self):
        _decl, out = run([(0, "X(3) = 0x3ull;", True), (4, "if (zf) TAIL(f_7100003000)", True), (8, "return;", False)])
        self.assertRegex(out[1], r"^if \(zf\) \{ c->x\[3\] = x_3; +TAIL\(f_7100003000\) \}$")

    def test_setjmp_spills_all_and_reloads_after_longjmp(self):
        _decl, out = run([(0, "X(19) = 0x9ull;", True), (4, "if (!RT_SETJMP(*rt_setjmp(c))) X(0) = 0;", True), (8, "return;", False)])
        self.assertIn("c->x[19] = x_19;", out[1])
        self.assertIn("else { x_0 = c->x[0]; x_19 = c->x[19];", out[1])


if __name__ == '__main__':
    unittest.main()
