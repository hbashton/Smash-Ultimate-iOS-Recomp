"""Ranked play: Matchmake Referee (120) - the game's per-player statistics and rating ("category" is
the game's own key, e.g. a fighter), rounds started and ended around each online match - with
ratings the server computes itself (Elo) and keeps, and matchmaking that prefers close ratings.

Rating: everyone starts at the game's initialRatingValue (or 1500). After a round, every pair
of players with different results moves by K * (result - expected), expected from the rating
difference (400-point logistic); a free-for-all is the average over the other players.

Clients are not trusted: a round is started by a player of its gathering, for players of that
gathering only, and ended by one of its players; a result counts only for the round's players.
The first report of a round counts (whether every player's game reports is not known yet); a
later one that differs is logged as a mismatch. Players keep statistics only for themselves, a
bounded number of them."""
import os
import time

from . import rmc
from .server import JsonStore
from .streams import StreamIn, StreamOut

K = 32.0
DEFAULT_RATING = 1500
MAX_RATING = 99999               # ratings (and the game's initial value) stay within 1..MAX_RATING
MAX_CATEGORIES = 128             # statistics one player may have (one a category)
MAX_STATS = 50000                # statistics kept in all
MAX_TARGETS = 256                # statistics asked for in one call
MAX_ROUNDS = 4096                # rounds kept (the oldest go first)
ROUND_LIFETIME = 3 * 3600.0      # seconds a round is kept
WIN, LOSS, DRAW = 1, 2, 3       # (roundWinLoss as the game reports it - checked against the logs)
STAT_FIELDS = ("recentDisconnection", "recentViolation", "recentMismatch", "recentWin", "recentLoss", "recentDraw",
               "totalDisconnect", "totalViolation", "totalMismatch", "totalWin", "totalLoss", "totalDraw")


def clamp_rating(v):
    return max(1, min(MAX_RATING, int(v)))


class Ranked(JsonStore):
    def __init__(self, nex, state_dir):
        super().__init__(os.path.join(state_dir, "nex_ratings.json") if state_dir else None)
        self.nex = nex
        self.stats = self.load() or {}   # "pid:category" -> dict
        self.of_pid = {}                 # pid -> {category: the same dicts}
        for e in self.stats.values():
            self.of_pid.setdefault(e["pid"], {})[e["category"]] = e
        self.rounds = {}         # round id -> {"gid", "category", "pids", "ended", "by", "at"}, oldest first
        self.next_round = int(time.time()) << 16
        r = nex.register
        r(120, 1, self.start_round)
        r(120, 2, self.get_start_round_param)
        r(120, 3, self.end_round)
        r(120, 4, self.end_round_without_report)
        r(120, 8, self.get_stats_primary)
        r(120, 9, self.get_stats_primaries)
        r(120, 10, self.get_stats_all)
        r(120, 11, self.create_stats)
        r(120, 12, self.get_or_create_stats)

    def content(self):
        return self.stats

    def tick(self, now):
        """Forgets rounds older than ROUND_LIFETIME."""
        while self.rounds:
            rid, r = next(iter(self.rounds.items()))
            if now - r["at"] < ROUND_LIFETIME and len(self.rounds) <= MAX_ROUNDS:
                break
            del self.rounds[rid]

    @staticmethod
    def fresh(pid, category, initial=None):
        e = {f: 0 for f in STAT_FIELDS}
        e.update(uniqueId=(pid << 24 ^ category) & 0xFFFFFFFFFFFFFFFF, category=category, pid=pid,
                 ratingValue=clamp_rating(initial) if initial else DEFAULT_RATING)
        return e

    def entry(self, pid, category, initial=None):
        """pid's statistics for category, made (and kept) if it has none; None if no more may be kept."""
        mine = self.of_pid.get(pid, {})
        e = mine.get(category)
        if e is None:
            if len(mine) >= MAX_CATEGORIES or len(self.stats) >= MAX_STATS:
                return None
            e = self.stats[f"{pid}:{category}"] = self.fresh(pid, category, initial)
            self.of_pid.setdefault(pid, {})[category] = e
            self.dirty = True
        return e

    def look(self, pid, category):
        """pid's statistics for category as they are (fresh ones, not kept, if it has none)."""
        return self.of_pid.get(pid, {}).get(category) or self.fresh(pid, category)

    def rating(self, pid):
        """A player's rating for matchmaking: the category the game asked about last, else the best."""
        mine = self.of_pid.get(pid)
        if not mine:
            return DEFAULT_RATING
        conn = self.nex.by_pid.get(pid)
        cat = conn.user.get("category") if conn else None
        if cat in mine:
            return mine[cat]["ratingValue"]
        return max(e["ratingValue"] for e in mine.values())

    @staticmethod
    def write_stats(o, e):
        def body(s):
            s.u64(e["uniqueId"])
            s.u32(e["category"])
            s.pid(e["pid"])
            for f in STAT_FIELDS:
                s.u32(e[f])
            s.u32(e["ratingValue"])
        o.struct(body)

    # ---- methods --------------------------------------------------------------------
    def get_or_create_stats(self, conn, s):
        _v, raw = s.struct()
        p = StreamIn(raw)
        category, initial = p.u32(), p.u32()
        conn.user["category"] = category
        e = self.entry(conn.pid, category, initial) or self.fresh(conn.pid, category, initial)
        self.nex.info(f"{self.nex.name(conn)}: stats for category {category}: rating {e['ratingValue']} "
                      f"({e['totalWin']} won, {e['totalLoss']} lost)")
        out = StreamOut()
        self.write_stats(out, e)
        return out.get()

    def create_stats(self, conn, s):
        _v, raw = s.struct()
        p = StreamIn(raw)
        self.entry(conn.pid, p.u32(), p.u32())
        return b""

    def target(self, s):
        _v, raw = s.struct()
        p = StreamIn(raw)
        return p.pid(), p.u32()

    def get_stats_primary(self, conn, s):
        pid, category = self.target(s)
        out = StreamOut()
        self.write_stats(out, self.look(pid, category))
        return out.get()

    def get_stats_primaries(self, conn, s):
        n = s.u32()
        if n > MAX_TARGETS:
            raise rmc.NexError(rmc.CORE_INVALID_ARGUMENT, f"{n} statistics")
        targets = [self.target(s) for _ in range(n)]
        out = StreamOut()
        out.u32(len(targets))
        for pid, category in targets:
            self.write_stats(out, self.look(pid, category))
        out.list([rmc.SUCCESS] * len(targets), StreamOut.u32)
        return out.get()

    def get_stats_all(self, conn, s):
        pid, _category = self.target(s)
        mine = list(self.of_pid.get(pid, {}).values())
        out = StreamOut()
        out.u32(len(mine))
        for e in mine:
            self.write_stats(out, e)
        return out.get()

    def start_round(self, conn, s):
        _v, raw = s.struct()
        p = StreamIn(raw)
        category, gid, pids = p.u32(), p.u32(), p.list(StreamIn.pid)
        mm = getattr(self.nex, "matchmaking", None)
        ms = mm.sessions.get(gid) if mm else None
        if ms is None or conn.pid not in ms.participants:
            raise rmc.NexError(rmc.RENDEZVOUS_NOT_PARTICIPATED, f"not in gathering {gid}")
        players = [pid for pid in dict.fromkeys(pids) if pid in ms.participants]
        if len(players) != len(pids):
            self.nex.info(f"round in gathering {gid} from {self.nex.name(conn)}: {len(pids) - len(players)} "
                          f"of its players are not in the gathering")
        self.tick(self.nex.now)
        rid = self.next_round
        self.next_round += 1
        self.rounds[rid] = {"gid": gid, "category": category, "pids": players, "ended": False, "by": conn.pid,
                            "at": self.nex.now}
        self.nex.info(f"round {rid} started by {self.nex.name(conn)}: category {category}, gathering {gid}, players {players}")
        others = [pid for pid in players if pid != conn.pid]
        if mm:
            mm.notify(others, 116000, conn.pid, rid)
        out = StreamOut()
        out.u64(rid)
        return out.get()

    def get_start_round_param(self, conn, s):
        r = self.rounds.get(s.u64())
        if r is None:
            raise rmc.NexError(0x80690002, "no such round")
        out = StreamOut()

        def body(o):
            o.u32(r["category"])
            o.u32(r["gid"])
            o.list(r["pids"], StreamOut.pid)
        out.struct(body)
        return out.get()

    def in_round(self, conn, r):
        if conn.pid not in r["pids"] and conn.pid != r["by"]:
            raise rmc.NexError(rmc.RENDEZVOUS_PERMISSION_DENIED, "not a player of that round")

    def end_round(self, conn, s):
        _v, raw = s.struct()
        p = StreamIn(raw)
        rid = p.u64()

        def result(q):
            _v2, raw2 = q.struct()
            r = StreamIn(raw2)
            return {"pid": r.pid(), "flag": r.u32(), "winloss": r.u32(), "change": r.s32(), "buffer": r.qbuffer()}
        results = p.list(result)
        r = self.rounds.get(rid)
        if r is None:
            self.nex.info(f"round {rid} ended by {self.nex.name(conn)}: no such round")
            return b""
        self.in_round(conn, r)
        report = {}                       # (one result a player of the round, the first)
        for x in results:
            if x["pid"] in r["pids"]:
                report.setdefault(x["pid"], x)
        self.nex.info(f"round {rid} ended by {self.nex.name(conn)}: "
                      + ", ".join(f"pid {x['pid']} {x['winloss']} ({x['change']:+d}, flag {x['flag']}, "
                                  f"{x['buffer'][:32].hex()})" for x in report.values())
                      + (f"; {len(results) - len(report)} other results ignored" if len(results) > len(report) else ""))
        outcome = {pid: x["winloss"] for pid, x in report.items()}
        if r["ended"]:
            if r.get("outcome") is not None and outcome != r["outcome"]:
                self.nex.info(f"round {rid}: MISMATCH - {self.nex.name(conn)} reports {outcome}, "
                              f"pid {r['reporter']} reported {r['outcome']} (the first counts)")
            return b""
        r["ended"], r["outcome"], r["reporter"] = True, outcome, conn.pid
        self.apply(r["category"], list(report.values()))
        return b""

    def end_round_without_report(self, conn, s):
        rid = s.u64()
        r = self.rounds.get(rid)
        if r is not None:
            self.in_round(conn, r)
            r["ended"] = True
        return b""

    # ---- rating ---------------------------------------------------------------------
    def apply(self, category, results):
        """New ratings from one result a player (all worked out before any is changed)."""
        score = {WIN: 1.0, LOSS: 0.0, DRAW: 0.5}
        known = [x for x in results if x["winloss"] in score]
        if len(known) < 2:
            return
        entries = {x["pid"]: self.entry(x["pid"], category) for x in known}
        if any(e is None for e in entries.values()):
            self.nex.info(f"ratings for category {category} not kept: too many statistics")
            return
        before = {pid: e["ratingValue"] for pid, e in entries.items()}
        after = {}
        for x in known:
            delta = 0.0
            for y in known:
                if y is x:
                    continue
                diff = max(-4000, min(4000, before[y["pid"]] - before[x["pid"]]))
                expected = 1 / (1 + 10 ** (diff / 400))
                delta += K * (score[x["winloss"]] - expected)
            after[x["pid"]] = clamp_rating(round(before[x["pid"]] + delta / (len(known) - 1)))
        for x in known:
            e = entries[x["pid"]]
            e["ratingValue"] = after[x["pid"]]
            field = {WIN: "Win", LOSS: "Loss", DRAW: "Draw"}[x["winloss"]]
            e["total" + field] += 1
            e["recent" + field] += 1
            self.nex.info(f"rating of pid {x['pid']} (category {category}): {before[x['pid']]} -> {e['ratingValue']}")
        self.dirty = True
