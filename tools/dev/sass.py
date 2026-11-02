#!/usr/bin/env python3
"""Maxwell (SM 5.x) shader binaries: opcode decoding, listing and census.

A program is a 0x50-byte header (SPH) followed by 64-bit instructions in groups of four,
the first of each group being scheduling control rather than an instruction. Opcodes are
identified by the top 16 bits (patterns below; '-' is don't-care), the more specific
pattern winning.

Usage:
  sass.py census <dir with dumped shaders>
  sass.py list <shader.bin>
"""
import os
import struct
import sys
from collections import Counter

PATTERNS = """
AL2P 1110111110100--- ALD 1110111111011--- AST 1110111111110--- ATOM_cas 111011101111----
ATOM 11101101-------- ATOMS_cas 11101110-------- ATOMS 11101100-------- B2R 1111000010111---
BAR 1111000010101--- BFE_reg 0101110000000--- BFE_cbuf 0100110000000--- BFE_imm 0011100-00000---
BFI_reg 0101101111110--- BFI_rc 0101001111110--- BFI_cr 0100101111110--- BFI_imm 0011011-11110---
BPT 111000111010---- BRA 111000100100---- BRK 111000110100---- BRX 111000100101----
CAL 111000100110---- CCTL 11101111011----- CCTLL 11101111100----- CONT 111000110101----
CS2R 0101000011001--- CSET 0101000010011--- CSETP 0101000010100--- DADD_reg 0101110001110---
DADD_cbuf 0100110001110--- DADD_imm 0011100-01110--- DEPBAR 1111000011110--- DFMA_reg 010110110111----
DFMA_rc 010100110111---- DFMA_cr 010010110111---- DFMA_imm 0011011-0111---- DMNMX_reg 0101110001010---
DMNMX_cbuf 0100110001010--- DMNMX_imm 0011100-01010--- DMUL_reg 0101110010000--- DMUL_cbuf 0100110010000---
DMUL_imm 0011100-10000--- DSET_reg 010110010------- DSET_cbuf 010010010------- DSET_imm 0011001-0-------
DSETP_reg 010110111000---- DSETP_cbuf 010010111000---- DSETP_imm 0011011-1000---- EXIT 111000110000----
F2F_reg 0101110010101--- F2F_cbuf 0100110010101--- F2F_imm 0011100-10101--- F2I_reg 0101110010110---
F2I_cbuf 0100110010110--- F2I_imm 0011100-10110--- FADD_reg 0101110001011--- FADD_cbuf 0100110001011---
FADD_imm 0011100-01011--- FADD32I 000010---------- FCHK_reg 0101110010001--- FCHK_cbuf 0100110010001---
FCHK_imm 0011100-10001--- FCMP_reg 010110111010---- FCMP_rc 010100111010---- FCMP_cr 010010111010----
FCMP_imm 0011011-1010---- FFMA_reg 010110011------- FFMA_rc 010100011------- FFMA_cr 010010011-------
FFMA_imm 0011001-1------- FFMA32I 000011---------- FLO_reg 0101110000110--- FLO_cbuf 0100110000110---
FLO_imm 0011100-00110--- FMNMX_reg 0101110001100--- FMNMX_cbuf 0100110001100--- FMNMX_imm 0011100-01100---
FMUL_reg 0101110001101--- FMUL_cbuf 0100110001101--- FMUL_imm 0011100-01101--- FMUL32I 00011110--------
FSET_reg 01011000-------- FSET_cbuf 01001000-------- FSET_imm 0011000--------- FSETP_reg 010110111011----
FSETP_cbuf 010010111011---- FSETP_imm 0011011-1011---- FSWZADD 0101000011111--- GETCRSPTR 111000101100----
GETLMEMBASE 111000101101---- HADD2_reg 0101110100010--- HADD2_cbuf 0111101-1------- HADD2_imm 0111101-0-------
HADD2_32I 0010110--------- HFMA2_reg 0101110100000--- HFMA2_rc 01100---1------- HFMA2_cr 01110---1-------
HFMA2_imm 01110---0------- HFMA2_32I 0010100--------- HMUL2_reg 0101110100001--- HMUL2_cbuf 0111100-1-------
HMUL2_imm 0111100-0------- HMUL2_32I 0010101--------- HSET2_reg 0101110100011--- HSET2_cbuf 0111110-1-------
HSET2_imm 0111110-0------- HSETP2_reg 0101110100100--- HSETP2_cbuf 0111111-1------- HSETP2_imm 0111111-0-------
I2F_reg 0101110010111--- I2F_cbuf 0100110010111--- I2F_imm 0011100-10111--- I2I_reg 0101110011100---
I2I_cbuf 0100110011100--- I2I_imm 0011100-11100--- IADD_reg 0101110000010--- IADD_cbuf 0100110000010---
IADD_imm 0011100-00010--- IADD3_reg 010111001100---- IADD3_cbuf 010011001100---- IADD3_imm 0011100-1100----
IADD32I 0001110--------- ICMP_reg 010110110100---- ICMP_rc 010100110100---- ICMP_cr 010010110100----
ICMP_imm 0011011-0100---- IDE 111000111001---- IDP_reg 0101001111111--- IDP_imm 0101001111011---
IMAD_reg 010110100------- IMAD_rc 010100100------- IMAD_cr 010010100------- IMAD_imm 0011010-0-------
IMAD32I 100000---------- IMADSP_reg 010110101------- IMADSP_rc 010100101------- IMADSP_cr 010010101-------
IMADSP_imm 0011010-1------- IMNMX_reg 0101110000100--- IMNMX_cbuf 0100110000100--- IMNMX_imm 0011100-00100---
IMUL_reg 0101110000111--- IMUL_cbuf 0100110000111--- IMUL_imm 0011100-00111--- IMUL32I 00011111--------
IPA 11100000-------- ISBERD 1110111111010--- ISCADD_reg 0101110000011--- ISCADD_cbuf 0100110000011---
ISCADD_imm 0011100-00011--- ISCADD32I 000101---------- ISET_reg 010110110101---- ISET_cbuf 010010110101----
ISET_imm 0011011-0101---- ISETP_reg 010110110110---- ISETP_cbuf 010010110110---- ISETP_imm 0011011-0110----
JCAL 111000100010---- JMP 111000100001---- JMX 111000100000---- KIL 111000110011----
LD 100------------- LDC 1110111110010--- LDG 1110111011010--- LDL 1110111101000---
LDS 1110111101001--- LEA_hi_reg 0101101111011--- LEA_hi_cbuf 000110---------- LEA_lo_reg 0101101111010---
LEA_lo_cbuf 010010111101---- LEA_lo_imm 0011011-11010--- LEPC 0101000011010--- LONGJMP 111000110001----
LOP_reg 0101110001000--- LOP_cbuf 0100110001000--- LOP_imm 0011100-01000--- LOP3_reg 0101101111100---
LOP3_cbuf 0000001--------- LOP3_imm 001111---------- LOP32I 000001---------- MEMBAR 1110111110011---
MOV_reg 0101110010011--- MOV_cbuf 0100110010011--- MOV_imm 0011100-10011--- MOV32I 000000010000----
MUFU 0101000010000--- NOP 0101000010110--- OUT_reg 1111101111100--- OUT_cbuf 1110101111100---
OUT_imm 1111011-11100--- P2R_reg 0101110011101--- P2R_cbuf 0100110011101--- P2R_imm 0011100011101---
PBK 111000101010---- PCNT 111000101011---- PEXIT 111000100011---- PIXLD 1110111111101---
PLONGJMP 111000101000---- POPC_reg 0101110000001--- POPC_cbuf 0100110000001--- POPC_imm 0011100-00001---
PRET 111000100111---- PRMT_reg 010110111100---- PRMT_rc 010100111100---- PRMT_cr 010010111100----
PRMT_imm 0011011-1100---- PSET 0101000010001--- PSETP 0101000010010--- R2B 1111000011000---
R2P_reg 0101110011110--- R2P_cbuf 0100110011110--- R2P_imm 0011100-11110--- RAM 111000111000----
RED 1110101111111--- RET 111000110010---- RRO_reg 0101110010010--- RRO_cbuf 0100110010010---
RRO_imm 0011100-10010--- RTT 111000110110---- S2R 1111000011001--- SAM 111000110111----
SEL_reg 0101110010100--- SEL_cbuf 0100110010100--- SEL_imm 0011100-10100--- SETCRSPTR 111000101110----
SETLMEMBASE 111000101111---- SHF_l_reg 0101101111111--- SHF_l_imm 0011011-11111--- SHF_r_reg 0101110011111---
SHF_r_imm 0011100-11111--- SHFL 1110111100010--- SHL_reg 0101110001001--- SHL_cbuf 0100110001001---
SHL_imm 0011100-01001--- SHR_reg 0101110000101--- SHR_cbuf 0100110000101--- SHR_imm 0011100-00101---
SSY 111000101001---- ST 101------------- STG 1110111011011--- STL 1110111101010---
STP 111011101010---- STS 1110111101011--- SUATOM 111010100------- SUATOM_cas 111010101-------
SULD 1110101100------ SURED 1110101101------ SUST 1110101100------ SYNC 1111000011111---
TEX 11000----------- TEX_b 1101111010------ TEXS 1101-00--------- TLD 11011100--------
TLD_b 11011101-------- TLD4 110010---------- TLD4_b 1101111011------ TLD4S 11011111-0------
TLDS 1101-01--------- TMML 1101111101011--- TMML_b 1101111101100--- TXA 1101111101000---
TXD 1101111000------ TXD_b 1101111001------ TXQ 1101111101001--- TXQ_b 1101111101010---
VABSDIFF 01010100-------- VABSDIFF4 010100000------- VADD 001000---------- VMAD 01011111--------
VMNMX 0011101--------- VOTE 0101000011011--- VOTE_vtg 0101000011100--- VSET 0100000---------
VSETP 0101000011110--- VSHL 01010111-------- VSHR 01010110-------- XMAD_reg 0101101100------
XMAD_rc 010100010------- XMAD_cr 0100111--------- XMAD_imm 0011011-00------
""".split()
# NB: SUST is "1110 1011 001- ----" in the source table; fix the entry above.
TABLE = []
for name, pat in zip(PATTERNS[::2], PATTERNS[1::2]):
    if name == "SUST":
        pat = "1110101100 1-----".replace(" ", "")[:16]
    mask = int("".join("0" if c == "-" else "1" for c in pat), 2)
    value = int(pat.replace("-", "0"), 2)
    TABLE.append((bin(mask).count("1"), mask, value, name))
TABLE.sort(reverse=True)


def opcode(insn):
    top = insn >> 48
    for _bits, mask, value, name in TABLE:
        if top & mask == value:
            return name
    return "?"


def instructions(blob, start=0x50):
    """(index, address, 64-bit word) for each instruction, skipping control words."""
    for a in range(start, len(blob) - 7, 8):
        if (a - start) // 8 % 4 == 0:
            continue
        yield a, struct.unpack_from("<Q", blob, a)[0]


def program(blob):
    """The instructions up to the end of the program: past every EXIT that no branch
    target lies beyond, stopping at the 'BRA to self' padding."""
    out, furthest = [], 0
    for a, w in instructions(blob):
        if w == 0xE2400FFFFF87000F:
            break
        op = opcode(w)
        out.append((a, w, op))
        if op in ("BRA", "SSY", "PBK", "CAL", "PCNT"):
            off = (w >> 20) & 0xFFFFFF
            if off & 0x800000:
                off -= 0x1000000
            furthest = max(furthest, a + 8 + off)
        if op == "EXIT" and (w >> 16) & 0xF == 7 and a >= furthest:   # unconditional, nothing beyond
            break
    return out


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "census":
        ops, per_shader = Counter(), Counter()
        files = sorted(f for f in os.listdir(path) if f.endswith(".bin"))
        for f in files:
            prog = program(open(os.path.join(path, f), "rb").read())
            names = [op for _a, _w, op in prog]
            ops.update(names)
            per_shader.update(set(names))
        print(f"{len(files)} shaders, {sum(ops.values())} instructions")
        for op, n in ops.most_common():
            print(f"  {op:12} {n:7}  in {per_shader[op]} shaders")
    elif cmd == "list":
        for a, w, op in program(open(path, "rb").read()):
            print(f"{a:06x}: {w:016x} {op}")


if __name__ == "__main__":
    main()
