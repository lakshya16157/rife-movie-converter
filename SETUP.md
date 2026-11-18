# Set up RIFE Movie Converter

## Requirements

- Windows 10 or 11 on a 64-bit x86 PC with AVX2.
- An NVIDIA GPU and a recent driver that supports CUDA 13. The setup selects a TensorRT builder library for the GPU. The export pipeline was tested on an RTX 3090 (compute capability 8.6).
- [64-bit Python 3.12](https://www.python.org/downloads/release/python-31210/) with Tcl/Tk, pip, and the Windows py launcher.
- [64-bit 7-Zip](https://www.7-zip.org/), installed in its default location or on PATH.
- Internet access and several GB of free space for the initial setup. The two TensorRT archives alone are about 2.5 GiB. Allow additional space on the drive where you save exports.

## Install and run

1. Clone or download the repository and extract it somewhere you can write files, such as your Desktop.
2. Double-click **Set Up RIFE.cmd**. It creates a local Python environment, installs the packages listed in **requirements.txt**, and downloads the runtime components from their upstream releases.
3. Double-click **Open RIFE Movie Converter.cmd**. Pick a movie and try **Export first 30 sec**. TensorRT builds its engine the first time it sees a new frame size.

After setup, conversion and playback run offline. To reinstall after moving the folder to another PC, rename `runtime/work-python` and run setup there. Each PC builds its own TensorRT engines.

On an existing installation, run **Set Up RIFE.cmd** once after updating to install the portable VapourSynth R73 host beside FFmpeg. Reopen the app. The export log shows `Frame transport: embedded VapourSynth` when FFmpeg can load it. Older runtimes automatically use the existing Y4M pipe.

## Where the files go

- **runtime** holds the model, local dependencies, caches, and logs. A fresh clone creates that folder during setup. It is excluded from Git.
- The finished movie is saved wherever you choose in the export dialog.
- **Open export log** opens the diagnostic log if an export fails.
