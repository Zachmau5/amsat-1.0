import numpy as np
from gnuradio import gr

class blk(gr.sync_block):
    """
    Simple NBFM Receive in an Embedded Python Block.

    Parameters
    ----------
    samp_rate  : float
        Complex input / audio output sample rate (e.g., 48e3)
    tau        : float
        De-emphasis time constant (seconds), e.g., 75e-6 or 750e-6
    max_dev    : float
        Max FM deviation (Hz), e.g., 5e3 for narrowband FM
    audio_gain : float
        Extra gain applied after de-emphasis (use to avoid clipping)
    """

    def __init__(self,
                 samp_rate=48e3,
                 tau=750e-6,
                 max_dev=5e3,
                 audio_gain=0.1):

        gr.sync_block.__init__(
            self,
            name="embedded_nbfm_rx",
            in_sig=[np.complex64],
            out_sig=[np.float32],
        )

        # Store parameters
        self.samp_rate  = float(samp_rate)
        self.tau        = float(tau)
        self.max_dev    = float(max_dev)
        self.audio_gain = float(audio_gain)

        # Quadrature demod gain (same as GNU Radio NBFM)
        self.gain = self.samp_rate / (2.0 * np.pi * self.max_dev)

        # De-emphasis coefficient at samp_rate
        dt = 1.0 / self.samp_rate
        self.alpha = dt / (self.tau + dt)

        # Internal state
        self._prev     = 0+0j   # previous complex sample for phase diff
        self._de_state = 0.0    # de-emphasis IIR state

    def work(self, input_items, output_items):
        x = input_items[0]
        y = output_items[0]

        if len(x) == 0:
            return 0

        # ---- Quadrature demod ----
        x_full = np.concatenate(([self._prev], x))
        diff   = x_full[1:] * np.conj(x_full[:-1])
        phase  = np.angle(diff).astype(np.float32)
        self._prev = x_full[-1]

        fm = self.gain * phase  # instantaneous frequency (audio-band)

        # ---- De-emphasis (single-pole IIR) ----
        out = np.empty_like(fm, dtype=np.float32)
        s = self._de_state
        a = self.alpha
        for i, v in enumerate(fm):
            s = s + a * (v - s)
            out[i] = s
        self._de_state = s

        # ---- Apply audio gain + soft limiting ----
        out *= self.audio_gain
        out = np.tanh(out)

        # ---- Write to output ----
        n = min(len(out), len(y))
        y[:n] = out[:n]
        return n
