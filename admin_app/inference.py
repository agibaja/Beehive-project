from pathlib import Path
from threading import Lock

import albumentations as A
import cv2
import numpy as np
import segmentation_models_pytorch as smp
import streamlit as st
import torch
from albumentations.pytorch import ToTensorV2


MODEL_PATH = (
    Path(__file__).resolve().parent
    / "models"
    / "best_floorplan_unet.pth"
)

VAL_TRANSFORM = A.Compose([
    A.Resize(512, 512),
    A.Normalize(
        mean=(0.485, 0.456, 0.406),
        std=(0.229, 0.224, 0.225),
    ),
    ToTensorV2(),
])


@st.cache_resource
def load_model(path, modified_at):
    # modified_at permite recargar si sustituyes los pesos.
    device = torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    model = smp.Unet(
        encoder_name="resnet34",
        encoder_weights=None,
        in_channels=3,
        classes=3,
    )

    weights = torch.load(
        path,
        map_location="cpu",
        weights_only=True,
    )

    model.load_state_dict(weights)
    model.to(device)
    model.eval()

    return model, device, Lock()


def predict_rooms(image_bytes, min_area=120):
    if not MODEL_PATH.is_file():
        raise FileNotFoundError(
            f"No se encuentra el modelo en: {MODEL_PATH}"
        )

    # OpenCV lee BGR, igual que en tu prueba.
    encoded = np.frombuffer(image_bytes, dtype=np.uint8)
    original = cv2.imdecode(encoded, cv2.IMREAD_COLOR)

    if original is None:
        raise ValueError("No se pudo leer la imagen.")

    original = cv2.cvtColor(original, cv2.COLOR_BGR2RGB)
    height, width = original.shape[:2]

    if height * width > 25_000_000:
        raise ValueError(
            "El plano supera el límite de 25 millones de píxeles."
        )

    transformed = VAL_TRANSFORM(image=original)

    model, device, lock = load_model(
        str(MODEL_PATH),
        MODEL_PATH.stat().st_mtime_ns,
    )

    input_image = (
        transformed["image"]
        .unsqueeze(0)
        .to(device)
    )

    # El modelo se comparte entre sesiones.
    with lock, torch.inference_mode():
        logits = model(input_image)
        prediction = logits.argmax(dim=1)[0].cpu().numpy()

    prediction = cv2.resize(
        prediction.astype(np.uint8),
        (width, height),
        interpolation=cv2.INTER_NEAREST,
    )

    room_mask = (prediction == 1).astype(np.uint8)

    count, labels, stats, centroids = (
        cv2.connectedComponentsWithStats(
            room_mask,
            connectivity=8,
        )
    )

    valid_labels = [
        label
        for label in range(1, count)
        if stats[label, cv2.CC_STAT_AREA] >= min_area
    ]

    # Mostrar únicamente las regiones que pasan el filtro.
    filtered_mask = np.isin(labels, valid_labels)
    annotated = original.copy()

    overlay = original.copy()
    overlay[filtered_mask] = (
        0.6 * original[filtered_mask]
        + 0.4 * np.array([255, 0, 0])
    ).astype(np.uint8)

    rooms = []

    for number, label in enumerate(valid_labels, start=1):
        cx, cy = centroids[label]

        rooms.append({
            "deteccion": number,
            "area_px": int(stats[label, cv2.CC_STAT_AREA]),
            "centro_x": int(cx),
            "centro_y": int(cy),
        })

        cv2.putText(
            annotated,
            f"Sala {number}",
            (max(0, int(cx) - 35), max(20, int(cy))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 0, 0),
            2,
        )

    return {
        "original": original,
        "annotated": annotated,
        "mask": filtered_mask.astype(np.uint8) * 255,
        "overlay": overlay,
        "rooms": rooms,
    }