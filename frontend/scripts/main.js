const APP_CONFIG = window.APP_CONFIG ?? { API_BASE: "" };

const apiBase = () => {
  const base = (APP_CONFIG.API_BASE || "").trim().replace(/\/+$/, "");
  if (base) {
    return base;
  }
  // Sin API_BASE: mismo origen; si se abre por HTTP/XAMPP, usa el servicio IA en HTTPS :8000.
  if (window.location.port === "8000") {
    return window.location.origin;
  }
  return `https://${window.location.hostname}:8000`;
};

const apiUrl = (path) => `${apiBase()}${path}`;
const wsUrl = (path) => apiUrl(path).replace(/^http/i, "ws");

if ("serviceWorker" in navigator && (window.isSecureContext || window.location.hostname === "localhost")) {
  window.addEventListener("load", () => {
    const appBase = new URL(".", window.location.href);
    navigator.serviceWorker.register(new URL("sw.js", appBase)).catch((error) => {
      console.warn("No se pudo registrar el service worker:", error);
    });
  });
}

const installButton = document.querySelector("#install-app");
const installStatus = document.querySelector("#install-status");
const installSecureLink = document.querySelector("#install-secure-link");
let deferredInstallPrompt = null;

if (installButton && installStatus) {
  const isInstalled = window.matchMedia("(display-mode: standalone)").matches
    || navigator.standalone === true;

  if (isInstalled) {
    installButton.hidden = true;
    installStatus.hidden = false;
    installStatus.textContent = "Inventario Claro ya está instalada en este dispositivo.";
  } else {
    window.addEventListener("beforeinstallprompt", (event) => {
      event.preventDefault();
      deferredInstallPrompt = event;
    });

    window.addEventListener("appinstalled", () => {
      deferredInstallPrompt = null;
      installButton.hidden = true;
      installStatus.hidden = false;
      installStatus.textContent = "La app quedó instalada. La encontrarás en la pantalla de inicio.";
    });

    installButton.addEventListener("click", async () => {
      if (!deferredInstallPrompt) {
        if (!window.isSecureContext) {
          installStatus.textContent = "La instalación requiere HTTPS. Abre la versión segura de esta web y vuelve a intentarlo.";
          installStatus.hidden = false;
          if (installSecureLink instanceof HTMLAnchorElement) {
            installSecureLink.href = apiUrl("/");
            installSecureLink.target = "_blank";
            installSecureLink.rel = "noopener";
            installSecureLink.hidden = false;
          }
          return;
        }

        const isAppleMobile = /iPad|iPhone|iPod/.test(navigator.userAgent)
          || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
        installStatus.textContent = isAppleMobile
          ? "En Safari, toca Compartir y elige “Añadir a pantalla de inicio”."
          : "Abre el menú del navegador y elige “Instalar app” o “Añadir a pantalla de inicio”.";
        installStatus.hidden = false;
        return;
      }

      const installPrompt = deferredInstallPrompt;
      deferredInstallPrompt = null;
      try {
        await installPrompt.prompt();
        const choice = await installPrompt.userChoice;
        installStatus.textContent = choice.outcome === "accepted"
          ? "La instalación se inició."
          : "Instalación cancelada. Puedes intentarlo desde el menú del navegador.";
        installStatus.hidden = false;
      } catch (error) {
        installStatus.textContent = `No se pudo iniciar la instalación: ${error.message}`;
        installStatus.hidden = false;
      }
    });
  }
}

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
const startCameraButton = document.querySelector("#audit-start-camera");
const stopCameraButton = document.querySelector("#audit-stop-camera");
const liveContainer = document.querySelector("#audit-live-container");
const liveVideo = document.querySelector("#audit-live-video");
const liveCanvas = document.querySelector("#audit-live-canvas");
const secureAccessLink = document.querySelector("#audit-secure-link");

const setMetric = (selector, value) => {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = String(value);
  }
};

const renderMetrics = (result) => {
  const percentage = result.porcentaje_ocupacion ?? result.ocupacion_porcentaje;
  const occupancyDisplay = result.ocupacion_display
    ?? (percentage == null ? "N/D — REVISAR" : `${percentage}%`);
  setMetric("#audit-occupancy", occupancyDisplay);
  setMetric("#audit-bottles", result.botellas_ok);
  setMetric("#audit-empty", result.espacios_vacios);
  setMetric("#audit-missing", result.faltantes_estimados);

  const state = document.querySelector("#audit-state");
  if (state) {
    state.textContent = result.estado;
    const stateClass = {
      OPTIMO: "is-optimo",
      RESURTIR: "is-resurtir",
      REVISAR: "is-revisar",
      "ESTANTE VACIO": "is-vacio",
    }[result.estado] || "";
    state.className = `audit-state ${stateClass}`;
  }

  updateAttention(result);
  updateBrands(result);
};
const updateBrands = (result) => {
  const brands = document.querySelector("#audit-brands");
  if (!brands) {
    return;
  }
  const found = Object.entries(result.productos_identificados ?? {});
  const unknown = result.productos_sin_identificar ?? 0;
  const parts = found.map(([brand, count]) => `${brand} ×${count}`);
  if (unknown > 0 && found.length) {
    parts.push(`${unknown} sin identificar`);
  }
  brands.hidden = parts.length === 0;
  brands.textContent = parts.length ? `En el anaquel: ${parts.join(", ")}` : "";
};
const updateAttention = (result) => {
  const attention = document.querySelector("#audit-attention");
  if (!attention) {
    return;
  }

  const messages = [];
  if (result.advertencia) {
    messages.push(result.advertencia);
  } else if (result.espacios_vacios > 0) {
    messages.push(
      `${result.espacios_vacios} espacio(s) vacío(s); ${result.faltantes_estimados} faltante(s) estimado(s)`,
    );
  }

  const damaged = result.producto_danado ?? result.productos_danados ?? 0;
  const fallen = result.producto_caido ?? result.productos_caidos ?? 0;
  if (damaged + fallen > 0) {
    messages.push(`${damaged} dañado(s) y ${fallen} caído(s)`);
  }

  attention.classList.toggle("is-revisar", result.estado === "REVISAR");
  attention.hidden = messages.length === 0;
  attention.textContent = messages.length ? `Atención: ${messages.join("; ")}` : "";
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
      const body = new FormData();
      body.append("file", image);

      const response = await fetch(apiUrl("/auditar"), { method: "POST", body });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail || `El servidor respondió con HTTP ${response.status}.`);
      }

      const result = await response.json();
      renderMetrics(result);

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

const showApiCertificateLink = () => {
  if (!(secureAccessLink instanceof HTMLAnchorElement)) {
    return;
  }

  secureAccessLink.href = apiUrl("/");
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
    renderMetrics(result);
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

      const socketUrl = wsUrl("/ws/auditar");
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
          auditStatus.textContent = "El servidor está activo, pero no puede inferir: falta models/best.pt o models/best_v3.pt.";
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

// ---------- Asistente ----------

const chatLog = document.querySelector("#chat-log");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");
const chatChips = document.querySelector("#chat-chips");

if (chatLog && chatForm && chatInput instanceof HTMLTextAreaElement && chatChips) {
  const sendButton = chatForm.querySelector(".chat-send");
  // El servidor reintenta solo si la API de NVIDIA se atora; aquí se le da margen para hacerlo.
  const TIMEOUT_MS = 45000;
  const FORMAS_PAGO = ["efectivo", "transferencia", "tarjeta", "credito"];
  const MOTIVOS = ["dañado", "caducado", "robo", "otro"];
  const ETIQUETAS = {
    producto: "Producto",
    cantidad: "Cantidad (piezas)",
    costo_unitario: "Costo por pieza",
    precio_venta: "Precio de venta",
    proveedor: "Proveedor",
    forma_pago: "Forma de pago",
    motivo: "Motivo",
  };
  const historial = [];
  let busy = false;

  const pesos = (valor) => `$${valor.toFixed(2)}`;
  const porcentaje = (valor) => `${valor.toFixed(2)} %`;
  const scrollToEnd = () => {
    chatLog.scrollTop = chatLog.scrollHeight;
  };

  const recordar = (rol, texto) => {
    historial.push({ rol, texto });
    historial.splice(0, Math.max(0, historial.length - 6));
  };

  const addBubble = (text, type) => {
    const bubble = document.createElement("li");
    bubble.className = `chat-bubble ${type.split(" ").map((t) => `is-${t}`).join(" ")}`;
    bubble.textContent = text.replace(/ (%|c\/u)/g, " $1");
    chatLog.append(bubble);
    scrollToEnd();
    return bubble;
  };

  const setBusy = (value) => {
    busy = value;
    if (sendButton) {
      sendButton.disabled = value;
    }
    chatChips.querySelectorAll("button").forEach((chip) => {
      chip.disabled = value;
    });
  };

  const showTyping = () => {
    const bubble = document.createElement("li");
    bubble.className = "chat-bubble is-bot is-typing";
    bubble.setAttribute("aria-label", "El asistente está trabajando");
    bubble.innerHTML = '<span class="chat-dots" aria-hidden="true"><span></span><span></span><span></span></span><span class="chat-typing-text"></span>';
    const label = bubble.querySelector(".chat-typing-text");
    chatLog.append(bubble);
    scrollToEnd();
    const timers = [
      window.setTimeout(() => { label.textContent = "Consultando tu inventario…"; }, 2000),
      window.setTimeout(() => { label.textContent = "Preparando la respuesta…"; }, 5000),
      window.setTimeout(() => { label.textContent = "El modelo está tardando más de lo normal; sigo intentando…"; }, 10000),
    ];
    return () => {
      timers.forEach((timer) => window.clearTimeout(timer));
      bubble.remove();
    };
  };

  const postJson = async (path, body) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const response = await fetch(apiUrl(path), body === undefined
        ? { signal: controller.signal }
        : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "Revisa los datos de la tarjeta.";
        throw new Error(response.status === 422 ? "Faltan datos o hay un valor inválido. Revisa la tarjeta." : detail);
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error("El servidor tardó más de lo esperado en responder. Revisa tu conexión e intenta de nuevo.");
      }
      if (error instanceof TypeError) {
        if (window.location.protocol === "file:") {
          throw new Error("Abriste la página como archivo. Arranca el servidor (python api_auditoria.py) y entra a https://localhost:8000/#asistente.");
        }
        const certHint = apiBase() !== window.location.origin
          ? ` Si usas XAMPP, abre ${apiUrl("/")} en otra pestaña para aceptar el certificado HTTPS.`
          : "";
        throw new Error(`No pude conectar con el servidor en ${apiBase()}. Revisa que esté activo.${certHint}`);
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  };

  // ----- Tarjeta editable -----

  // Una sola línea de ganancia, sin hablar de medios de pago (eso va en el chat).
  const lineasMargen = (costo, precio) => {
    const ganancia = precio - costo;
    const margen = (ganancia / precio) * 100;
    if (ganancia <= 0) return [`Pierdes ${pesos(-ganancia)} por pieza.`];
    const lineas = [`Ganas ${pesos(ganancia)} por pieza (${porcentaje(margen)}).`];
    if (margen < 20) lineas.push(`Margen bajo: a ${pesos((costo * 4) / 3)} ganarías 25 %.`);
    return lineas;
  };

  const crearCampo = (nombre, valor, tarjeta) => {
    const wrapper = document.createElement("label");
    wrapper.className = "card-field";
    const titulo = document.createElement("span");
    titulo.textContent = ETIQUETAS[nombre];
    wrapper.append(titulo);

    let control;
    if (nombre === "producto" && tarjeta.accion !== "ENTRADA") {
      control = document.createElement("select");
      control.append(new Option("Elige un producto", ""));
      tarjeta.productos.forEach((p) => control.append(new Option(p.nombre, p.id)));
      control.value = tarjeta.producto_id ?? "";
    } else if (nombre === "forma_pago" || nombre === "motivo") {
      control = document.createElement("select");
      (nombre === "forma_pago" ? FORMAS_PAGO : MOTIVOS).forEach((o) => control.append(new Option(o, o)));
      control.value = valor;
    } else {
      control = document.createElement("input");
      const numerico = ["cantidad", "costo_unitario", "precio_venta"].includes(nombre);
      control.type = numerico ? "number" : "text";
      if (numerico) {
        control.inputMode = nombre === "cantidad" ? "numeric" : "decimal";
        control.min = nombre === "cantidad" ? "1" : "0.01";
        control.step = nombre === "cantidad" ? "1" : "0.01";
      }
      if (nombre === "producto") {
        const lista = document.createElement("datalist");
        lista.id = `productos-${Date.now()}`;
        tarjeta.productos.forEach((p) => lista.append(new Option(p.nombre)));
        wrapper.append(lista);
        control.setAttribute("list", lista.id);
      }
      control.value = valor ?? "";
    }
    control.name = nombre;
    control.className = "card-input";
    if (tarjeta.faltantes.includes(nombre)) {
      wrapper.classList.add("is-missing");
      titulo.textContent += " · falta";
    } else if (tarjeta.sugeridos?.includes(nombre)) {
      wrapper.classList.add("is-suggested");
      titulo.textContent += " · último";
    }
    wrapper.append(control);
    return wrapper;
  };

  const renderTarjeta = (tarjeta) => {
    const item = document.createElement("li");
    item.className = "chat-card";
    const form = document.createElement("form");
    form.noValidate = true;
    const titulo = document.createElement("h3");
    titulo.textContent = tarjeta.titulo;
    const campos = document.createElement("div");
    campos.className = "card-fields";
    // Proveedor y forma de pago tienen valor por defecto: van plegados para que la tarjeta sea breve.
    const extras = document.createElement("details");
    extras.className = "card-more";
    const extrasTitulo = document.createElement("summary");
    extrasTitulo.textContent = "Proveedor y forma de pago";
    const extrasCampos = document.createElement("div");
    extrasCampos.className = "card-fields";
    extras.append(extrasTitulo, extrasCampos);
    Object.entries(tarjeta.campos).forEach(([nombre, valor]) => {
      const destino = ["proveedor", "forma_pago"].includes(nombre) ? extrasCampos : campos;
      destino.append(crearCampo(nombre, valor, tarjeta));
    });
    if (extrasCampos.childElementCount) campos.append(extras);
    const resumen = document.createElement("ul");
    resumen.className = "card-summary";
    const aviso = document.createElement("p");
    aviso.className = "card-warning";
    aviso.hidden = true;
    const acciones = document.createElement("div");
    acciones.className = "card-actions";
    const confirmar = document.createElement("button");
    confirmar.type = "submit";
    confirmar.className = "button button-primary";
    confirmar.textContent = "Confirmar y guardar";
    const descartar = document.createElement("button");
    descartar.type = "button";
    descartar.className = "button button-outline";
    descartar.textContent = "Descartar";
    acciones.append(confirmar, descartar);
    form.append(titulo, campos, resumen, aviso, acciones);
    item.append(form);
    chatLog.append(item);

    const valor = (nombre) => form.elements.namedItem(nombre)?.value.trim() ?? "";
    const numero = (nombre) => Number.parseFloat(valor(nombre));
    const productoElegido = () => {
      if (tarjeta.accion === "ENTRADA") {
        const nombre = valor("producto").toLowerCase();
        return tarjeta.productos.find((p) => p.nombre.toLowerCase() === nombre) ?? null;
      }
      return tarjeta.productos.find((p) => p.id === valor("producto")) ?? null;
    };

    const actualizar = () => {
      const producto = productoElegido();
      const lineas = [];
      let error = "";
      const cantidad = numero("cantidad");
      if (tarjeta.accion === "ENTRADA") {
        const costo = numero("costo_unitario");
        const precio = numero("precio_venta");
        if (cantidad > 0) {
          const total = costo > 0 ? ` · total ${pesos(cantidad * costo)}` : "";
          lineas.push(producto
            ? `Existencia ${producto.existencia} → ${producto.existencia + cantidad}${total}`
            : `Producto nuevo: ${cantidad} piezas${total}`);
        }
        if (costo > 0 && precio > 0) lineas.push(...lineasMargen(costo, precio));
      } else if (tarjeta.accion === "CAMBIO_PRECIO" && producto) {
        const precio = numero("precio_venta");
        lineas.push(`Precio ${pesos(producto.precio_venta)} → ${precio > 0 ? pesos(precio) : "?"}`);
        if (precio > 0 && producto.costo_promedio > 0) lineas.push(...lineasMargen(producto.costo_promedio, precio));
      } else if (tarjeta.accion === "MERMA" && producto && cantidad > 0) {
        if (cantidad > producto.existencia) {
          error = `Solo tienes ${producto.existencia} piezas de ${producto.nombre}.`;
        } else {
          lineas.push(`Existencia ${producto.existencia} → ${producto.existencia - cantidad} · pierdes ${pesos(cantidad * producto.costo_promedio)}`);
        }
      }

      resumen.replaceChildren(...lineas.map((texto) => {
        const li = document.createElement("li");
        li.textContent = texto;
        return li;
      }));
      resumen.hidden = lineas.length === 0;

      const obligatorios = { ENTRADA: ["producto", "cantidad", "costo_unitario", "precio_venta"], CAMBIO_PRECIO: ["producto", "precio_venta"], MERMA: ["producto", "cantidad"] }[tarjeta.accion];
      const completos = obligatorios.every((nombre) => (nombre === "producto" ? valor(nombre) !== "" : numero(nombre) > 0));
      form.querySelectorAll(".card-field").forEach((campo) => {
        const control = campo.querySelector(".card-input");
        const vacio = obligatorios.includes(control.name) && (control.name === "producto" ? control.value === "" : !(Number.parseFloat(control.value) > 0));
        campo.classList.toggle("is-missing", vacio);
      });
      aviso.textContent = error;
      aviso.hidden = !error;
      confirmar.disabled = !completos || Boolean(error);
      scrollToEnd();
    };

    form.addEventListener("input", actualizar);
    form.addEventListener("change", actualizar);
    actualizar();

    descartar.addEventListener("click", () => {
      item.remove();
      addBubble("Listo, no se guardó nada.", "bot");
      recordar("asistente", "El usuario descartó la tarjeta; no se guardó nada.");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (confirmar.disabled) return;
      const producto = productoElegido();
      const datos = { accion: tarjeta.accion };
      Object.keys(tarjeta.campos).forEach((nombre) => {
        if (nombre === "producto") return;
        const numerico = ["cantidad", "costo_unitario", "precio_venta"].includes(nombre);
        datos[nombre] = numerico ? numero(nombre) : valor(nombre);
      });
      datos.producto = tarjeta.accion === "ENTRADA" ? (producto?.nombre ?? valor("producto")) : producto?.nombre;
      datos.producto_id = producto?.id ?? null;

      confirmar.disabled = true;
      descartar.disabled = true;
      confirmar.textContent = "Guardando…";
      try {
        const { mensaje } = await postJson("/confirmar", datos);
        item.remove();
        addBubble(`✓ ${mensaje}`, "bot success");
        recordar("asistente", mensaje);
      } catch (error) {
        aviso.textContent = error.message;
        aviso.hidden = false;
        confirmar.textContent = "Confirmar y guardar";
        descartar.disabled = false;
        confirmar.disabled = false;
      }
    });
    scrollToEnd();
  };

  // ----- Lista de resurtido (sin modelo: sale directo de Firestore) -----

  const textoResurtido = ({ productos, total_estimado: total }) => [
    "Pedido de resurtido:",
    ...productos.map((p) => `• ${p.pedir} × ${p.producto}${p.gancho ? " (urgente)" : ""}`),
    "",
    `Costo estimado: ${pesos(total)}`,
  ].join("\n");

  const mostrarResurtido = async () => {
    if (busy) return;
    addBubble("Lista de resurtido", "user");
    setBusy(true);
    const hideTyping = showTyping();
    try {
      const lista = await postJson("/resurtido");
      hideTyping();
      if (!lista.productos.length) {
        addBubble("Todo está arriba del mínimo. No hace falta resurtir por ahora.", "bot");
        return;
      }
      const texto = textoResurtido(lista);
      const bubble = addBubble(texto, "bot");
      bubble.style.whiteSpace = "pre-line";
      const compartir = document.createElement("a");
      compartir.className = "button button-primary chat-share";
      compartir.href = `https://wa.me/?text=${encodeURIComponent(texto)}`;
      compartir.target = "_blank";
      compartir.rel = "noopener";
      compartir.textContent = "Enviar por WhatsApp";
      bubble.append(document.createElement("br"), compartir);
      recordar("asistente", texto);
      scrollToEnd();
    } catch (error) {
      hideTyping();
      addBubble(error.message, "bot error");
    } finally {
      setBusy(false);
    }
  };

  // ----- Conversación -----

  const ask = async (message) => {
    const text = message.trim();
    if (!text || busy) return;

    addBubble(text, "user");
    chatInput.value = "";
    chatInput.style.height = "";
    setBusy(true);
    const hideTyping = showTyping();
    const previos = historial.slice();
    recordar("usuario", text);

    try {
      const respuesta = await postJson("/asistente", { mensaje: text, historial: previos });
      hideTyping();
      addBubble(respuesta.mensaje, "bot");
      recordar("asistente", respuesta.mensaje);
      if (respuesta.tarjeta) {
        chatLog.querySelectorAll(".chat-card").forEach((card) => card.remove());
        renderTarjeta(respuesta.tarjeta);
      }
    } catch (error) {
      hideTyping();
      addBubble(error.message, "bot error");
    } finally {
      setBusy(false);
    }
  };

  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(chatInput.value);
  });

  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      ask(chatInput.value);
    }
  });

  chatInput.addEventListener("input", () => {
    chatInput.style.height = "";
    chatInput.style.height = `${Math.min(chatInput.scrollHeight, 120)}px`;
  });

  chatChips.addEventListener("click", (event) => {
    const chip = event.target instanceof Element ? event.target.closest(".chat-chip") : null;
    if (!chip) return;
    if (chip.dataset.accion === "resurtido") mostrarResurtido();
    else ask(chip.textContent ?? "");
  });
}

// Botón de regresar del asistente: vuelve a la sección anterior o al inicio.
const chatBack = document.querySelector("#chat-back");
if (chatBack) {
  let llegoDesdeLaPagina = false;
  window.addEventListener("hashchange", (event) => {
    if (new URL(event.newURL).hash === "#asistente") {
      llegoDesdeLaPagina = new URL(event.oldURL).pathname === window.location.pathname;
    }
  });
  chatBack.addEventListener("click", () => {
    if (llegoDesdeLaPagina) {
      window.history.back();
    } else {
      window.location.hash = "#inicio";
    }
  });
}
