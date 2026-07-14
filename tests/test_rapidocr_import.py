"""Test RapidOCR 3.7.0 + onnxruntime 1.17.1 compatibility"""
from rapidocr import RapidOCR
import numpy as np

engine = RapidOCR()
print(f"RapidOCR init OK")

# Quick smoke test on synthetic image
img = np.zeros((100, 400, 3), dtype=np.uint8)
img[30:70, 50:350] = 255
result = engine(img)
print(f"Result: {result}")
print("PASS")
