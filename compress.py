import os
import cv2
import subprocess
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

# ===========================
INPUT = "input"
OUTPUT = "output"

QUALITY = 42
SPEED = 6

USE_DENOISE = False
USE_SHARPEN = False
RESIZE = None          # contoh (1920,1080)

THREADS = os.cpu_count()
# ===========================

Path(OUTPUT).mkdir(exist_ok=True)

gpu = cv2.cuda.getCudaEnabledDeviceCount() > 0

if gpu:
    print("CUDA GPU ditemukan")
else:
    print("CUDA tidak tersedia")

files = []

for ext in ("*.jpg","*.jpeg","*.png"):
    files.extend(Path(INPUT).glob(ext))
    files.extend(Path(INPUT).glob(ext.upper()))


def process(file):

    img = cv2.imread(str(file), cv2.IMREAD_COLOR)

    if img is None:
        return

    if gpu:

        g = cv2.cuda_GpuMat()
        g.upload(img)

        if RESIZE:
            g = cv2.cuda.resize(g, RESIZE)

        if USE_DENOISE:
            # Gaussian Blur ringan
            g = cv2.cuda.createGaussianFilter(
                cv2.CV_8UC3,
                cv2.CV_8UC3,
                (3,3),
                0
            ).apply(g)

        img = g.download()

    else:

        if RESIZE:
            img = cv2.resize(img, RESIZE)

    if USE_SHARPEN:

        kernel = (
            [[0,-1,0],
             [-1,5,-1],
             [0,-1,0]]
        )

        import numpy as np

        img = cv2.filter2D(
            img,
            -1,
            np.array(kernel)
        )

    tmp = tempfile.NamedTemporaryFile(
        suffix=".png",
        delete=False
    )

    cv2.imwrite(tmp.name, img)

    output = Path(OUTPUT)/(file.stem+".avif")

    subprocess.run([
        "avifenc",
        "--min", str(QUALITY),
        "--max", str(QUALITY),
        "--speed", str(SPEED),
        "--jobs", "all",
        tmp.name,
        str(output)
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL)

    os.unlink(tmp.name)


with ThreadPoolExecutor(max_workers=THREADS) as pool:

    list(
        tqdm(
            pool.map(process, files),
            total=len(files)
        )
    )

print("Selesai.")