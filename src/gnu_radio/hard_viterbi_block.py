"""
Hard-decision CCSDS K=7 Viterbi decoder as an Embedded Python PDU block.

Input:
    PDU containing float32 soft symbols from matrix_deinterleaver_soft
Output:
    PDU containing uint8 bytes (one decoded bit per byte)
"""

import numpy as np
from gnuradio import gr
import pmt

# ============================================================
# Convolutional code parameters: CCSDS K=7, r = 1/2
# Generators: 171(octal), 133(octal)
# GNU Radio uses bit-reversed polynomials: 79, -109
# ============================================================

K = 7
MEM = K - 1
NUM_STATES = 2 ** MEM  # 64 states

# Precompute next_state and encoder output for each state and input bit
next_state = np.zeros((NUM_STATES, 2), dtype=int)
out_table = np.zeros((NUM_STATES, 2, 2), dtype=int)  # [state][bit] -> [e0, e1]

# Bit-reversed CCSDS polynomials in decimal, exactly as cc_decoder uses
POLY0 = 79     # 0b1001111 (reversed 171o)
POLY1 = -109   # 0b1101101 (reversed 133o, sign = inversion)

def parity(x: int) -> int:
    """Return parity (0/1) of integer x."""
    return bin(x).count("1") & 1

# Build trellis tables to match gr::fec::code::cc_decoder
for s in range(NUM_STATES):
    for bit in (0, 1):
        # 7-bit shift register, NEW BIT IN LSB (matches 2*state logic)
        # reg = [d_{n-6} ... d_{n-1}, bit]
        reg = ((s << 1) | bit) & 0x7F

        # Generator 0 output
        e0 = parity(reg & abs(POLY0))
        if POLY0 < 0:
            e0 ^= 1

        # Generator 1 output
        e1 = parity(reg & abs(POLY1))
        if POLY1 < 0:
            e1 ^= 1

        out_table[s, bit] = [e0, e1]

        # Next state = drop oldest bit d_{n-6}, keep [d_{n-5} ... d_n]
        next_s = reg & 0x3F
        next_state[s, bit] = next_s



# ============================================================
# Viterbi Decoder (hard decision)
# ============================================================

def viterbi_decode_k7_ccsds(encoded_bits: np.ndarray) -> np.ndarray:
    """
    Hard-decision Viterbi for CCSDS K=7, r=1/2.

    encoded_bits : 1D numpy array of 0/1 ints, even length
    returns      : decoded 0/1 bits (INCLUDING the 6 tail bits)
    """
    encoded_bits = np.array(encoded_bits, dtype=np.uint8)
    num_steps = len(encoded_bits) // 2

    metrics = np.full((num_steps, NUM_STATES), np.inf)
    prev_state = np.zeros((num_steps, NUM_STATES), dtype=int)
    prev_bit = np.zeros((num_steps, NUM_STATES), dtype=int)

    # Initial path metrics: known start state 0 (terminated mode)
    prev_metrics = np.full(NUM_STATES, np.inf)
    prev_metrics[0] = 0.0

    # Forward recursion
    for t in range(num_steps):
        r0 = int(encoded_bits[2 * t])
        r1 = int(encoded_bits[2 * t + 1])
        metrics[t, :] = np.inf

        for s_prev in range(NUM_STATES):
            m_prev = prev_metrics[s_prev]
            if np.isinf(m_prev):
                continue

            for bit in (0, 1):
                e0, e1 = out_table[s_prev, bit]
                # Hamming distance branch metric
                branch = (r0 != e0) + (r1 != e1)

                s_next = next_state[s_prev, bit]
                cand = m_prev + branch

                if cand < metrics[t, s_next]:
                    metrics[t, s_next] = cand
                    prev_state[t, s_next] = s_prev
                    prev_bit[t, s_next] = bit

        prev_metrics = metrics[t]

    # Traceback: choose best final state (end_state = -1 behavior)
    state = int(np.argmin(prev_metrics))
    decoded_rev = []

    for t in range(num_steps - 1, -1, -1):
        bit = prev_bit[t, state]
        decoded_rev.append(bit)
        state = prev_state[t, state]

    decoded = decoded_rev[::-1]
    return np.array(decoded, dtype=np.uint8)


# ============================================================
# Embedded Python PDU block
# ============================================================

class blk(gr.basic_block):
    """
    hard_viterbi_pdu

    Input:  PDU with f32vector of soft symbols (matrix_deinterleaver_soft output)
    Output: PDU with u8vector of decoded bits (0/1 per byte, tail bits removed)
    """

    def __init__(self):
        gr.basic_block.__init__(
            self,
            name="hard_viterbi_pdu",
            in_sig=None,
            out_sig=None,
        )

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self.handle_msg)

        self.message_port_register_out(pmt.intern("out"))

    def handle_msg(self, msg):
        # Extract PDU
        meta = pmt.car(msg)
        vec = pmt.cdr(msg)

        # 1) Extract float soft symbols
        soft = np.array(pmt.f32vector_elements(vec), dtype=np.float32)

        # 2) Convert soft symbols → hard bits (0 or 1)
        bits = (soft >= 0.0).astype(np.uint8)

        # 3) Run hard-decision Viterbi
        decoded_bits = viterbi_decode_k7_ccsds(bits)
        # decoded_bits length ≈ 2566 (2560 info + 6 tail)

        # 4) Remove the 6 encoder tail bits (K−1 = 6)
        decoded_no_tail = decoded_bits[:-6]

        # 5) Take exactly 2560 info bits (no offset for noiseless test)
        FRAME_INFO_BITS = 2560
        info_bits = decoded_no_tail[:FRAME_INFO_BITS]

        # Safety fallback
        if len(info_bits) > FRAME_INFO_BITS:
            info_bits = info_bits[:FRAME_INFO_BITS]

        # 6) Build output PDU with 1 byte per bit
        out_vec = pmt.init_u8vector(len(info_bits), info_bits.tolist())
        out_pdu = pmt.cons(pmt.PMT_NIL, out_vec)

        # 7) Publish
        self.message_port_pub(pmt.intern("out"), out_pdu)
