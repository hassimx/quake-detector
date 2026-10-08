"""streaming detector: takes records in chunks and reports new alarms.

The detector itself is NOT changed: the filter, the STA/LTA windows and the
thresholds come from step11.py (the analyze function and the
ON_THR/OFF_THR/GROUP_SEC constants). Each time we only run it over a "sliding
buffer" of the last BUFFER_SEC seconds and keep the alarms we have not seen
yet. The filter and STA/LTA are causal (they only look into the past), so new
chunks do not change what was already computed.

Limits:
  * the first WARMUP_SEC seconds of a stream give no alarms (the LTA window and
    the filter are still "warming up"); the batch steps 11-13 had no such skip;
  * gaps in the stream are filled with zeros, as in step7 (st.merge(fill_value=0)).
"""

import numpy as np
from obspy import Trace
from obspy.signal.trigger import trigger_onset

from step11 import GROUP_SEC, OFF_THR, ON_THR, analyze

BUFFER_SEC = 300   # how many of the latest seconds we keep in the buffer
WARMUP_SEC = 60    # we do not trust the first seconds of the buffer
PEAK_SEC = 10      # the STA/LTA peak is looked for in the first 10 seconds after the alarm starts


class StreamDetector:
    """detector of one station"""

    def __init__(self):
        self.last_alarm = None  # time of the last accepted alarm (UTCDateTime)
        self._reset()

    def _reset(self):
        self.samples = np.empty(0)
        self.start = None  # time of the first sample in the buffer
        self.fs = None

    @property
    def end(self):
        """time right after the last sample of the buffer"""
        return self.start + len(self.samples) / self.fs

    def push(self, starttime, fs, samples):
        """adds a chunk of the record; returns the list of new alarms.

        Alarm: {"time": UTCDateTime, "peak": STA/LTA maximum}.
        """
        samples = np.asarray(samples, dtype=np.float64)
        if self.fs is not None and fs != self.fs:
            self._reset()  # sampling rate changed: start over
        if self.start is None:
            self.start, self.fs = starttime, fs
            self.samples = samples
        else:
            gap = round((starttime - self.end) * fs)
            if gap > BUFFER_SEC * fs:
                self._reset()  # gap too big: start the buffer over
                self.start, self.fs = starttime, fs
                self.samples = samples
            else:
                if gap > 0:
                    samples = np.concatenate([np.zeros(gap), samples])
                elif gap < 0:
                    samples = samples[-gap:]  # the chunk came with an overlap
                if len(samples) == 0:
                    return []
                self.samples = np.concatenate([self.samples, samples])
        excess = len(self.samples) - int(BUFFER_SEC * self.fs)
        if excess > 0:
            self.samples = self.samples[excess:]
            self.start = self.start + excess / self.fs
        return self._detect()

    def _detect(self):
        if len(self.samples) / self.fs <= WARMUP_SEC:
            return []
        tr = Trace(data=self.samples.copy(),
                   header=dict(sampling_rate=self.fs, starttime=self.start))
        _, ratio, _ = analyze(tr)
        new = []
        # we take the "raw" alarm starts and merge them ourselves (instead of taking the ready
        # list from analyze): merging must be relative to the last
        # accepted alarm of the stream, not to the start of the buffer
        for on, _off in trigger_onset(ratio, ON_THR, OFF_THR):
            t = self.start + on / self.fs
            if t < self.start + WARMUP_SEC:
                continue
            if self.last_alarm is None or t - self.last_alarm > GROUP_SEC:
                peak = float(ratio[on:on + int(PEAK_SEC * self.fs)].max())
                self.last_alarm = t
                new.append(dict(time=t, peak=peak))
        return new
