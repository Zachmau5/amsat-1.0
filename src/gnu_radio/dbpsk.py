"""
Embedded Python Blocks:

This block implements:
    prev = x[n-1]       (1-sample delay)
    z[n] = x[n] * conj(prev)
    y[n] = Re{ z[n] }

"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):
    """Delay-1, Multiply-Conjugate, and Real-part in one block"""

    def __init__(self):
        gr.sync_block.__init__(
            self,
            name='DBPSK Demod',   # name shown in GRC
            in_sig=[np.complex64],
            out_sig=[np.float32]           # Complex to Real result
        )

        # Internal state: previous sample for the 1-sample delay
        self.prev = np.complex64(0.0 + 0.0j)

    def work(self, input_items, output_items):
        x = input_items[0]           # complex input vector
        y = output_items[0]          # real output vector
        n = len(x)

        # Build array of "delayed" samples: [prev, x[0], x[1], ..., x[n-2]]
        delayed = np.empty_like(x)
        delayed[0] = self.prev
        delayed[1:] = x[:-1]

        # Multiply by conjugate of delayed signal and take real part
        y[:] = np.real(x * np.conj(delayed))

        # Update state for next call
        self.prev = x[-1]

        return n
