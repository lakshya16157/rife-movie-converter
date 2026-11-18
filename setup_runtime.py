"""Download the Windows tools and model used by RIFE Movie Converter."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PLUGINS = RUNTIME / "plugins"
MODELS = PLUGINS / "models" / "rife_v2"
CUDA = PLUGINS / "vsmlrt-cuda"
VSMLRT = "https://github.com/AmusementClub/vs-mlrt/releases/download"
TRT = f"{VSMLRT}/v15.16/vsmlrt-windows-x64-tensorrt.v15.16.7z"

CUDA_DLLS = (
    "cudart64_13.dll",
    "nvinfer_10.dll",
    "nvinfer_builder_resource_ptx_10.dll",
    "nvinfer_plugin_10.dll",
    "nvinfer_vc_plugin_10.dll",
    "nvonnxparser_10.dll",
    "nvrtc-builtins64_132.dll",
    "nvrtc64_130_0.dll",
    "trtexec.exe",
)


def run(*args, **options):
    return subprocess.run(args, check=True, **options)


def fetch(url, destination):
    print(f"Downloading {destination.name}...", flush=True)
    run("curl.exe", "--fail", "--location", "--silent", "--show-error", "--retry", "2",
        "--output", destination, url)


def extract(archive, destination, *options):
    destination.mkdir(parents=True, exist_ok=True)
    run("tar.exe", "-xf", archive, "-C", destination, *options)


def gpu_resource():
    output = run("nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader",
                 capture_output=True, text=True).stdout.splitlines()
    if not output:
        raise RuntimeError("No NVIDIA GPU was detected.")
    capability = output[0].strip().replace(".", "")
    if capability not in {"75", "80", "86", "89", "90", "100", "120"}:
        raise RuntimeError(f"The bundled TensorRT release has no builder resource for SM {capability}.")
    return f"nvinfer_builder_resource_sm{capability}_10.dll"


def install_tensor_rt(temp, resource):
    if (PLUGINS / "vstrt.dll").is_file() and all(
        (CUDA / name).is_file() for name in (*CUDA_DLLS, resource)
    ):
        return
    seven_zip = shutil.which("7z.exe") or Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    print("Downloading the two TensorRT archives (about 2.5 GiB total).", flush=True)
    for part in ("001", "002"):
        fetch(f"{TRT}.{part}", temp / f"tensorrt.7z.{part}")
    PLUGINS.mkdir(parents=True, exist_ok=True)
    members = ("vstrt.dll", *(f"vsmlrt-cuda/{name}" for name in (*CUDA_DLLS, resource)))
    if Path(seven_zip).is_file():
        run(seven_zip, "x", temp / "tensorrt.7z.001", f"-o{PLUGINS}", "-y",
            *(m.replace("/", "\\") for m in members))
        return
    # No 7-Zip: split volumes are a plain byte split, so join them and let Windows tar.exe extract.
    joined = temp / "tensorrt.7z"
    with open(joined, "wb") as out:
        for part in ("001", "002"):
            src = temp / f"tensorrt.7z.{part}"
            with open(src, "rb") as f:
                shutil.copyfileobj(f, out, 16 << 20)
            src.unlink()
    extract(joined, PLUGINS, *members)


def install_runtime(temp):
    resource = gpu_resource()
    if not (RUNTIME / "probe" / "ffmpeg.exe").is_file() or not (RUNTIME / "probe" / "ffprobe.exe").is_file():
        archive = temp / "ffmpeg.zip"
        fetch("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip", archive)
        extract(archive, RUNTIME / "probe", "--strip-components", "2", "--include", "*/bin/*")
    if not (RUNTIME / "mpv" / "mpv.exe").is_file():
        archive = temp / "mpv.7z"
        fetch("https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261111/"
              "mpv-x86_64-20261111-git-bcde6b93cc.7z", archive)
        extract(archive, RUNTIME / "mpv")
    if not (PLUGINS / "ffms2.dll").is_file():
        archive = temp / "ffms2.7z"
        fetch("https://github.com/FFMS/ffms2/releases/download/5.0/ffms2-5.0-msvc.7z", archive)
        extract(archive, PLUGINS, "--strip-components", "2", "--include", "*/x64/ffms2.dll")
    if not (PLUGINS / "akarin.dll").is_file():
        archive = temp / "akarin.7z"
        fetch("https://github.com/AkarinVS/vapoursynth-plugin/releases/download/v0.96/"
              "akarin-release-lexpr-amd64-v0.96g3.7z", archive)
        extract(archive, PLUGINS)
    if not (PLUGINS / "vszip.avx2.dll").is_file():
        run(sys.executable, "-m", "pip", "--disable-pip-version-check", "download",
            "--no-deps", "--only-binary=:all:", "--dest", temp, "vapoursynth-vszip==24.0.0")
        wheel = next(temp.glob("vapoursynth_vszip-24.0.0-*.whl"))
        with ZipFile(wheel) as package:
            member = "vapoursynth/plugins/vszip/vszip.avx2.dll"
            (PLUGINS / "vszip.avx2.dll").write_bytes(package.read(member))
            license_file = next(n for n in package.namelist() if n.endswith("/licenses/LICENSE"))
            (PLUGINS / "vszip-LICENSE.txt").write_bytes(package.read(license_file))
    if not (RUNTIME / "vsmlrt.py").is_file():
        script = temp / "vsmlrt.py"
        fetch("https://raw.githubusercontent.com/AmusementClub/vs-mlrt/v15.16/scripts/vsmlrt.py", script)
        shutil.copyfile(script, RUNTIME / "vsmlrt.py")
    if not (MODELS / "rife_v4.25.onnx").is_file():
        archive = temp / "rife.7z"
        fetch(f"{VSMLRT}/external-models/rife_v4.25.7z", archive)
        extract(archive, MODELS.parent, "--include", "rife_v2/*")
    lite_name = "rife_v4.25_lite.onnx"
    if not (MODELS / lite_name).is_file():
        archive = temp / "rife-lite.7z"
        fetch(f"{VSMLRT}/external-models/rife_v4.25_lite.7z", archive)
        extract(archive, temp, f"rife_v2/{lite_name}")
        shutil.move(temp / "rife_v2" / lite_name, MODELS / lite_name)
    if not (PLUGINS / "models" / "dpir" / "drunet_deblocking_color.onnx").is_file():
        archive = temp / "dpir.7z"  # optional deblocking model, about 480 MB download
        fetch(f"{VSMLRT}/model-20211209/dpir_v3.7z", archive)
        extract(archive, PLUGINS / "models", "dpir/drunet_deblocking_color.onnx")
    install_tensor_rt(temp, resource)
    run(RUNTIME / "probe" / "ffprobe.exe", "-version", stdout=subprocess.DEVNULL)
    print("Runtime ready.", flush=True)


if __name__ == "__main__":
    try:
        with TemporaryDirectory(prefix="rife-setup-") as staging:
            install_runtime(Path(staging))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"Runtime setup failed: {error}")
