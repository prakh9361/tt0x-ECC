# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

# =========================================================================
# PYTHON GOLDEN MODEL FOR GF(2^8) ECC
# =========================================================================
POLY = 0x11B
CURVE_A = 0x01

def gf_add(a, b):
    return a ^ b

def gf_mult(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        carry = a & 0x80
        a <<= 1
        if carry:
            a ^= POLY
        b >>= 1
    return p

def gf_inv(a):
    if a == 0: return 0
    for i in range(1, 256):
        if gf_mult(a, i) == 1:
            return i
    return 0

def point_double(x1, y1):
    lam = gf_add(x1, gf_mult(y1, gf_inv(x1)))
    lam_sq = gf_mult(lam, lam)
    x3 = gf_add(gf_add(lam_sq, lam), CURVE_A)
    
    x1_sq = gf_mult(x1, x1)
    lam_x3 = gf_mult(lam, x3)
    y3 = gf_add(gf_add(x1_sq, lam_x3), x3)
    return x3, y3

def point_add(x1, y1, x2, y2):
    lam = gf_mult(gf_add(y1, y2), gf_inv(gf_add(x1, x2)))
    lam_sq = gf_mult(lam, lam)
    x3 = gf_add(gf_add(gf_add(gf_add(lam_sq, lam), x1), x2), CURVE_A)
    
    x1_plus_x3 = gf_add(x1, x3)
    lam_x1_x3 = gf_mult(lam, x1_plus_x3)
    y3 = gf_add(gf_add(lam_x1_x3, x3), y1)
    return x3, y3

def ecc_scalar_mult_golden(k, xg, yg):
    if k == 0: return 0, 0
    msb = 7
    while msb >= 0 and not (k & (1 << msb)):
        msb -= 1
    if msb < 0: return 0, 0

    xr, yr = xg, yg
    for i in range(msb - 1, -1, -1):
        xr, yr = point_double(xr, yr)
        if k & (1 << i):
            xr, yr = point_add(xr, yr, xg, yg)
    return xr, yr

# =========================================================================
# COCOTB HARDWARE DRIVERS
# =========================================================================

async def reset_dut(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

async def load_params(dut, k, xg, yg):
    dut.ui_in.value = k
    dut.uio_in.value = 0b00000001
    await ClockCycles(dut.clk, 1)

    dut.ui_in.value = xg
    dut.uio_in.value = 0b00000010
    await ClockCycles(dut.clk, 1)

    dut.ui_in.value = yg
    dut.uio_in.value = 0b00000100
    await ClockCycles(dut.clk, 1)

    dut.uio_in.value = 0

async def start_and_wait(dut, timeout=500):
    dut.uio_in.value = 0b00001000
    await ClockCycles(dut.clk, 1)
    dut.uio_in.value = 0

    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if (int(dut.uio_out.value) >> 5) & 1:
            return True
    return False

def read_result(dut):
    return int(dut.uo_out.value)

# =========================================================================
# EXHAUSTIVE TEST
# =========================================================================

@cocotb.test()
async def test_exhaustive_keys(dut):
    """Exhaustively test all 256 possible scalar keys (k) against the golden model."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    # Base point coordinates to test against
    XG, YG = 0x53, 0xCA

    # Test every single key from 1 to 255
    # (k=0 maps to the point at infinity, which triggers the error flag in hardware)
    for k in range(1, 256):
        # 1. Compute expected result in Python
        expected_x, expected_y = ecc_scalar_mult_golden(k, XG, YG)

        # 2. Compute result in Verilog Hardware
        await load_params(dut, k=k, xg=XG, yg=YG)
        done = await start_and_wait(dut)
        
        assert done, f"Hardware timeout for k={k:#04x}"
        
        # Read X
        dut.uio_in.value = 0b00000000
        await ClockCycles(dut.clk, 1)
        hw_x = read_result(dut)

        # Read Y
        dut.uio_in.value = 0b00010000
        await ClockCycles(dut.clk, 1)
        hw_y = read_result(dut)

        # 3. Exhaustively Compare
        assert hw_x == expected_x, f"X Mismatch at k={k:#04x}! HW: {hw_x:#04x}, Expected: {expected_x:#04x}"
        assert hw_y == expected_y, f"Y Mismatch at k={k:#04x}! HW: {hw_y:#04x}, Expected: {expected_y:#04x}"
        
    dut._log.info("SUCCESS: All 255 K scalar operations completely match the Python golden model!")
