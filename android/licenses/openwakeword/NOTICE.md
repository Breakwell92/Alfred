# openWakeWord models

The files in `android/app/src/main/assets/wakeword/` come unmodified from the openWakeWord project, release v0.5.1: https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1

| File | What | Licence |
| --- | --- | --- |
| `melspectrogram.onnx` | Audio → mel spectrogram | openWakeWord's code is Apache 2.0; we treat the bundled model files as below |
| `embedding_model.onnx` | Mel spectrogram → speech embedding (based on Google's speech embedding model, Apache 2.0) | as above |
| `hey_jarvis_v0.1.onnx`, `alexa_v0.1.onnx`, `hey_mycroft_v0.1.onnx` | Stock wake word classifiers | CC BY-NC-SA 4.0 |

openWakeWord's README says its pre-trained models are licensed under Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (https://creativecommons.org/licenses/by-nc-sa/4.0/), because of the training data. Alfred is a private, personal, non-commercial app, which this licence allows. Don't ship these models in anything commercial.

Models by David Scripka and contributors (openWakeWord).
