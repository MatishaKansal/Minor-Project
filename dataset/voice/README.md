# Financial Audit Audio Test Dataset — 50 Synthetic Voice Notes

## Contents
- `audio/`: 50 mono, 16 kHz, 16-bit PCM WAV files (`AUD-001.wav` to `AUD-050.wav`).
- `transcripts/`: exact source text used to synthesize each clip.
- `metadata/audio_manifest.csv`: clip metadata, duration, synthesis profile and quality band.
- `metadata/ground_truth.csv`: one row per audio clip with expected financial fields.
- `metadata/field_ground_truth.csv`: long-form field/value labels for extraction scoring.
- `metadata/ground_truth.json`: complete structured labels and transcripts.

## Scenario coverage
The 50 clips cover payment confirmations, partial payments, overdue-payment follow-ups, invoice/PO amount differences, purchase-order and delivery updates, ledger/bank clarifications, missing-document requests and credit-note/return adjustments.

## Audio generation and limitations
All speech is synthetic, generated with eSpeak. Five synthesis profiles vary voice preset, pitch and speaking rate; they are not five real or independent human speakers. The audio is English TTS using Indian business and financial terminology, not naturally recorded Indian-accented speech. Do not describe this dataset as human-recorded speech or as representative of real speaker/accent variation.

Thirty clips are clean; fifteen contain mild additive background noise (nominal SNR 30 dB); five contain moderate additive background noise (nominal SNR 23 dB). Noise is synthetic white noise. The provided transcript is the exact text supplied to the TTS engine, not a human-verified transcription.

All business names, invoice identifiers and transaction details are fictional. Amounts and identifiers are intended for parser testing only. This dataset is not evidence of real financial transactions.

## Suggested evaluation
1. Run ASR on all 50 WAV files without supplying the reference transcripts.
2. Save ASR hypotheses separately; do not overwrite the provided references.
3. Report Word Error Rate (WER) and Character Error Rate (CER) against `transcripts/`.
4. Run the financial entity extractor on the ASR output and compare fields against `ground_truth.csv` or `field_ground_truth.csv`.
5. Report exact-match precision, recall and F1 for categorical/string fields; report exact match and absolute/relative error for numeric amounts.
6. Break results down by scenario category and audio-quality band. Because all samples are synthetic and use one TTS engine, results measure pipeline behavior on this controlled test set, not real-world speech-recognition performance.

## Reproducibility
The audio is standardized to 16,000 Hz, mono, signed 16-bit PCM WAV. `metadata/audio_manifest.csv` records durations and nominal quality bands.
