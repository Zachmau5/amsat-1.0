This directory contains all GNU Radio Companion (GRC) flowgraphs, DSP scripts, and analysis utilities developed for the AMSAT dual-band satellite ground-station project. These tools support FM demodulation, DBPSK decoding, symbol synchronization, telemetry extraction, and custom error-correction modules.

Directory Structure
gnu_radio/
├── ao73.wav
├── dbpsk.py
├── fm_receive.grc
├── fm_receive.py
├── funcube_telemetry_parser.py
├── hard_viterbi_block.py
├── iqtoreal.grc
├── iqtoreal.py
├── nbfm_receiver.py
├── tmp.txt
├── weakIQ.grc
└── weakIQ.py

📡 Overview

The GNU Radio tools here were used to:
Demodulate NBFM from the ISS
Decode BPSK/DBPSK signals from CubeSats
Experiment with timing recovery and symbol synchronization
Test CCSDS K=7 convolutional code decoding
Process weak IQ recordings for offline signal recovery
Extract and parse AO-73 (FUNcube-1) telemetry frames

📘 File Descriptions
ao73.wav
Raw IQ recording (complex baseband) used to test the BPSK/DBPSK demodulation chain.

dbpsk.py
Standalone Python implementation of a Differential BPSK demodulator.

fm_receive.grc / fm_receive.py
Full Narrowband FM Receive Chain, including:
Low-pass filtering
Quadrature discriminator
De-emphasis filter
Audio scaling/output

This chain was used to successfully demodulate multiple ISS downlink passes.

funcube_telemetry_parser.py

Extracts and decodes 256-byte FUNcube-1 telemetry frames after Viterbi + RS decoding.
Outputs human-readable frame fields and CSV logs.

hard_viterbi_block.py
A custom Embedded Python PDU block implementing:
CCSDS K=7 (constraint length 7)
Rate 1/2 Viterbi decoding
64-state trellis
Hard-decision input symbols

iqtoreal.grc / iqtoreal.py
Utilities for splitting or down-converting IQ recordings into real-valued streams for analysis or plotting.

nbfm_receiver.py
Pure-Python implementation of an NBFM demodulator used for embedding inside GNU Radio blocks.
Supports:

Quadrature discriminator

De-emphasis

Audio gain control

weakIQ.grc / weakIQ.py

Flowgraph and script for evaluating demodulation performance under weak-signal conditions.

Used to test:

Timing recovery behavior

Soft vs hard symbol slicing

FLL + RRC effects on eye-diagram opening
