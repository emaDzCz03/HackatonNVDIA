const menuToggle = document.querySelector(".menu-toggle");
const navigation = document.querySelector("#primary-navigation");

if (menuToggle && navigation) {
  const closeMenu = () => {
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.setAttribute("aria-label", "Abrir menú");
    navigation.classList.remove("is-open");
  };

  menuToggle.addEventListener("click", () => {
    const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
    menuToggle.setAttribute("aria-expanded", String(!isOpen));
    menuToggle.setAttribute("aria-label", isOpen ? "Abrir menú" : "Cerrar menú");
    navigation.classList.toggle("is-open", !isOpen);
  });

  navigation.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("a")) {
      closeMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMenu();
    }
  });
}

const auditImage = document.querySelector("#audit-image");
const auditPanel = document.querySelector("#audit-panel");
const auditStatus = document.querySelector("#audit-status");
const auditResults = document.querySelector("#audit-results");

const setMetric = (selector, value) => {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = String(value);
  }
};

const updateAttention = (result) => {
  const attention = document.querySelector("#audit-attention");
  if (!attention) {
    return;
  }

  const messages = [];
  if (result.espacios_vacios > 0) {
    messages.push(
      `${result.espacios_vacios} espacio(s) vacío(s); ${result.faltantes_estimados} faltante(s) estimado(s)`,
    );
  }

  const damaged = result.producto_danado ?? result.productos_danados ?? 0;
  const fallen = result.producto_caido ?? result.productos_caidos ?? 0;
  if (damaged + fallen > 0) {
    messages.push(`${damaged} dañado(s) y ${fallen} caído(s)`);
  }

  attention.hidden = messages.length === 0;
  attention.textContent = messages.length ? `Atención: ${messages.join("; ")}.` : "";
};

if (auditImage && auditPanel && auditStatus && auditResults) {
  auditImage.addEventListener("change", async () => {
    const image = auditImage.files?.[0];
    if (!image) {
      return;
    }

    auditPanel.setAttribute("aria-busy", "true");
    auditImage.disabled = true;
    if (startCameraButton) {
      startCameraButton.disabled = true;
    }
    auditStatus.textContent = "Analizando la imagen…";
    auditStatus.classList.remove("is-error");

    try {
      const apiUrl = `https://${window.location.hostname}:8000/auditar`;
      const body = new FormData();
      body.append("file", image);

      const response = await fetch(apiUrl, { method: "POST", body });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail || `El servidor respondió con HTTP ${response.status}.`);
      }

      const result = await response.json();
      setMetric("#audit-occupancy", `${result.ocupacion_porcentaje}%`);
      setMetric("#audit-bottles", result.botellas_ok);
      setMetric("#audit-empty", result.espacios_vacios);
      setMetric("#audit-missing", result.faltantes_estimados);

      const state = document.querySelector("#audit-state");
      if (state) {
        state.textContent = result.estado;
        state.className = `audit-state ${result.estado === "OPTIMO" ? "is-optimo" : result.estado === "RESURTIR" ? "is-resurtir" : "is-vacio"}`;
      }

      updateAttention(result);

      const annotatedImage = document.querySelector("#audit-annotated");
      if (annotatedImage) {
        annotatedImage.src = `data:image/jpeg;base64,${result.imagen_anotada}`;
        annotatedImage.hidden = false;
      }

      auditResults.hidden = false;
      auditStatus.textContent = "Auditoría completada.";
    } catch (error) {
      auditStatus.textContent = error instanceof TypeError
        ? "No se pudo conectar con la IA. Revisa que el servidor esté activo en el puerto 8000 y que ambos dispositivos estén en la misma red."
        : error.message;
      auditStatus.classList.add("is-error");
    } finally {
      auditPanel.setAttribute("aria-busy", "false");
      auditImage.disabled = false;
      if (startCameraButton) {
        startCameraButton.disabled = false;
      }
      auditImage.value = "";
    }
  });
}

const startCameraButton = document.querySelector("#audit-start-camera");
const stopCameraButton = document.querySelector("#audit-stop-camera");
const liveContainer = document.querySelector("#audit-live-container");
const liveVideo = document.querySelector("#audit-live-video");
const liveCanvas = document.querySelector("#audit-live-canvas");
const secureAccessLink = document.querySelector("#audit-secure-link");

const showApiCertificateLink = () => {
if (!(secureAccessLink instanceof HTMLAnchorElement)) {
  return;
}

secureAccessLink.href = `https://${window.location.hostname}:8000/`;
secureAccessLink.textContent = "Abrir servidor IA y aceptar certificado";
secureAccessLink.target = "_blank";
secureAccessLink.rel = "noopener";
secureAccessLink.hidden = false;
};

if (
  startCameraButton
  && stopCameraButton
  && liveContainer
  && liveVideo instanceof HTMLVideoElement
  && liveCanvas instanceof HTMLCanvasElement
  && auditStatus
  && auditResults
) {
  let cameraStream = null;
  let auditSocket = null;
  let captureCanvas = null;
  let nextFrameTimer = null;
  let connectionTimer = null;

  const stopLiveCamera = (statusMessage = null, closeSocket = true) => {
    if (nextFrameTimer !== null) {
      window.clearTimeout(nextFrameTimer);
      nextFrameTimer = null;
    }
    if (connectionTimer !== null) {
      window.clearTimeout(connectionTimer);
      connectionTimer = null;
    }

    const socket = auditSocket;
    auditSocket = null;
    if (socket) {
      socket.onclose = null;
      if (closeSocket && socket.readyState < WebSocket.CLOSING) {
        socket.close(1000, "Cámara detenida");
      }
    }

    cameraStream?.getTracks().forEach((track) => track.stop());
    cameraStream = null;
    if (auditImage) {
      auditImage.disabled = false;
    }
    liveVideo.srcObject = null;
    liveContainer.hidden = true;
    startCameraButton.hidden = false;
    startCameraButton.disabled = false;
    stopCameraButton.hidden = true;

    const context = liveCanvas.getContext("2d");
    context?.clearRect(0, 0, liveCanvas.width, liveCanvas.height);
    if (statusMessage) {
      auditStatus.textContent = statusMessage;
    }
  };

  if (secureAccessLink instanceof HTMLAnchorElement && !window.isSecureContext) {
    showApiCertificateLink();
  }

  const syncOverlaySize = () => {
    const width = liveVideo.videoWidth;
    const height = liveVideo.videoHeight;
    if (liveCanvas.width !== width || liveCanvas.height !== height) {
      liveCanvas.width = width;
      liveCanvas.height = height;
    }
    return { width, height };
  };

  const updateLiveMetrics = (result) => {
    setMetric("#audit-occupancy", `${result.ocupacion_porcentaje}%`);
    setMetric("#audit-bottles", result.botellas_ok);
    setMetric("#audit-empty", result.espacios_vacios);
    setMetric("#audit-missing", result.faltantes_estimados);

    const state = document.querySelector("#audit-state");
    if (state) {
      state.textContent = result.estado;
      state.className = `audit-state ${result.estado === "OPTIMO" ? "is-optimo" : result.estado === "RESURTIR" ? "is-resurtir" : "is-vacio"}`;
    }

    updateAttention(result);
    auditResults.hidden = false;
  };

  const drawDetections = (detections) => {
    const { width, height } = syncOverlaySize();
    const context = liveCanvas.getContext("2d");
    if (!context) {
      return;
    }

    context.clearRect(0, 0, width, height);
    const colors = {
      0: "#00dc00",
      1: "#ff0000",
      2: "#ff8c00",
      3: "#ffe600",
    };
    context.lineWidth = 2;
    context.font = "600 14px sans-serif";

    for (const detection of detections) {
      const [x1, y1, x2, y2] = detection.box;
      const left = x1 * width;
      const top = y1 * height;
      const boxWidth = (x2 - x1) * width;
      const boxHeight = (y2 - y1) * height;
      const color = colors[detection.class_id] || "#ffffff";
      context.strokeStyle = color;
      context.fillStyle = color;
      context.strokeRect(left, top, boxWidth, boxHeight);
      context.fillText(
        `${detection.label} ${(detection.conf * 100).toFixed(0)}%`,
        left,
        Math.max(14, top - 5),
      );
    }
  };

  const sendNextFrame = () => {
    const socket = auditSocket;
    if (
      !socket
      || socket.readyState !== WebSocket.OPEN
      || liveVideo.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
    ) {
      return;
    }

    const sourceWidth = liveVideo.videoWidth;
    const sourceHeight = liveVideo.videoHeight;
    if (!sourceWidth || !sourceHeight) {
      nextFrameTimer = window.setTimeout(sendNextFrame, 100);
      return;
    }

    captureCanvas ??= document.createElement("canvas");
    const scale = Math.min(1, 640 / sourceWidth);
    captureCanvas.width = Math.round(sourceWidth * scale);
    captureCanvas.height = Math.round(sourceHeight * scale);
    const captureContext = captureCanvas.getContext("2d");
    if (!captureContext) {
      stopLiveCamera("No se pudo preparar el fotograma de la cámara.");
      return;
    }

    captureContext.drawImage(liveVideo, 0, 0, captureCanvas.width, captureCanvas.height);
    captureCanvas.toBlob((blob) => {
      if (blob && auditSocket === socket && socket.readyState === WebSocket.OPEN) {
        socket.send(blob);
      }
    }, "image/jpeg", 0.7);
  };

  startCameraButton.addEventListener("click", async () => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      auditStatus.textContent = "El navegador bloquea la cámara en esta dirección HTTP. Abre la versión HTTPS del servidor para continuar.";
      auditStatus.classList.add("is-error");
      return;
    }

    startCameraButton.disabled = true;
    auditStatus.classList.remove("is-error");
    auditStatus.textContent = "Solicitando acceso a la cámara…";

    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
      if (auditImage) {
        auditImage.disabled = true;
      }
      const annotatedImage = document.querySelector("#audit-annotated");
      if (annotatedImage) {
        annotatedImage.hidden = true;
      }

      const metadataReady = new Promise((resolve, reject) => {
        if (liveVideo.readyState >= HTMLMediaElement.HAVE_METADATA) {
          resolve();
          return;
        }

        const handleMetadata = () => {
          liveVideo.removeEventListener("error", handleError);
          resolve();
        };
        const handleError = () => {
          liveVideo.removeEventListener("loadedmetadata", handleMetadata);
          reject(new Error("No se pudieron leer los metadatos de la cámara."));
        };
        liveVideo.addEventListener("loadedmetadata", handleMetadata, { once: true });
        liveVideo.addEventListener("error", handleError, { once: true });
      });
      liveVideo.srcObject = cameraStream;
      await metadataReady;
      await liveVideo.play();
      liveContainer.hidden = false;
      syncOverlaySize();

      const socketUrl = `wss://${window.location.hostname}:8000/ws/auditar`;
      auditSocket = new WebSocket(socketUrl);
      auditSocket.binaryType = "arraybuffer";
      auditStatus.textContent = "Conectando con el servidor de inferencia…";
      connectionTimer = window.setTimeout(() => {
        if (auditSocket?.readyState === WebSocket.CONNECTING) {
          auditStatus.textContent = "El servidor de inferencia no responde en el puerto 8000. Inícialo en la PC y vuelve a intentar.";
          auditStatus.classList.add("is-error");
          auditSocket.close();
        }
      }, 10000);
      auditSocket.onopen = () => {
        if (connectionTimer !== null) {
          window.clearTimeout(connectionTimer);
          connectionTimer = null;
        }
        auditStatus.textContent = "Cámara en vivo: analizando anaquel…";
        sendNextFrame();
      };
      auditSocket.onmessage = (event) => {
        const result = JSON.parse(event.data);
        if (result.error) {
          auditStatus.textContent = result.error;
          auditStatus.classList.add("is-error");
          sendNextFrame();
          return;
        }
        auditStatus.classList.remove("is-error");
        updateLiveMetrics(result);
        drawDetections(result.detections);
        sendNextFrame();
      };
      auditSocket.onerror = () => {
        auditStatus.textContent = "No se pudo abrir la conexión segura con la IA. Acepta el certificado del servidor y vuelve a intentar.";
        auditStatus.classList.add("is-error");
        showApiCertificateLink();
      };
      auditSocket.onclose = (event) => {
        const wasActive = cameraStream !== null;
        stopLiveCamera(null, false);
        if (wasActive && event.code === 1011) {
          auditStatus.textContent = "El servidor está activo, pero no puede inferir: falta el modelo models/best.pt.";
          auditStatus.classList.add("is-error");
        } else if (wasActive && event.code !== 1000) {
          auditStatus.textContent = event.code === 1006
            ? "Falló el WebSocket seguro. Acepta el certificado de la IA en el enlace y vuelve a intentar; si persiste, revisa la red y el firewall del puerto 8000."
            : `La conexión de auditoría terminó (${event.reason || event.code}).`;
          auditStatus.classList.add("is-error");
          if (event.code === 1006) {
            showApiCertificateLink();
          }
        }
      };
      startCameraButton.hidden = true;
      stopCameraButton.hidden = false;
    } catch (error) {
      stopLiveCamera();
      auditStatus.textContent = error instanceof DOMException && error.name === "NotAllowedError"
        ? "No se concedió permiso para usar la cámara."
        : `No se pudo iniciar la cámara: ${error.message}`;
      auditStatus.classList.add("is-error");
    }
  });

  stopCameraButton.addEventListener("click", () => {
    stopLiveCamera("Cámara detenida.");
  });

  window.addEventListener("resize", syncOverlaySize);
}
