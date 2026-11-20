# App de recepción de mercancía

Aplicación web para registrar entregas de mercancía mediante foto, revisión manual,
asistente conversacional y cálculos deterministas de margen e inventario.

## Alcance inicial

- Catálogo congelado de 10 a 15 productos comunes.
- Recepción de mercancía y actualización de existencias.
- Costo de compra, precio de venta y margen por pieza.
- Registro de forma de pago.
- Sin ventas ni control contable completo en esta fase.

## Tecnologías previstas

- Frontend: React con PWA.
- Base de datos y autenticación: Firebase.
- Almacenamiento de imágenes: Firebase Storage.
- Servicios auxiliares: Firebase Functions.
- Visión: modelo base permisivo, por definir tras revisar licencias disponibles.
- Asistente: modelo pequeño permisivo con ajuste LoRA/QLoRA.

## Estructura

- `frontend/`: interfaz web y PWA.
- `firebase/`: reglas, índices y Functions.
- `ml/vision/`: datos, entrenamiento y evaluación de visión.
- `ml/assistant/`: datos sintéticos, evaluación y adaptadores del asistente.
- `docs/`: arquitectura, datos, evaluación y casos de prueba.
- `tests/`: pruebas automatizadas.
- `api_auditoria.py`: microservicio local de inferencia para auditoría de anaqueles.

## Auditoría de anaqueles

El servicio FastAPI prefiere `models/best.engine` cuando CUDA está disponible;
en los demás casos carga `models/best.pt`. Si este archivo todavía no existe,
usa `models/best_v3.pt` como respaldo. El archivo `best.pt` incluido en
`models/modelo_auditoria_yolo.zip` ya está extraído en `models/best.pt`.
Los umbrales de confianza son 0.35 para botellas, 0.20 para espacios vacíos y
0.30 para productos dañados o caídos.

La ocupación se calcula con las detecciones de botellas y espacios vacíos. Si
no se detecta ningún espacio vacío, el resultado es `REVISAR` y muestra
`N/D — REVISAR`, nunca 100%. El endpoint `POST /auditar-lote` acepta varias
imágenes en el campo multipart `files` y devuelve los mismos resultados que
`POST /auditar`.

El script `test_vacios.py` cuenta cuántas imágenes contienen detecciones de
espacios vacíos. Por defecto busca imágenes tanto sueltas como dentro de ZIPs
en `docs/datos`; para usar otra carpeta, ejecuta
`python test_vacios.py --images-dir ruta\a\imagenes`.

En Windows, instala las dependencias y levanta el servicio desde la raíz del
repositorio. El primer arranque genera `cert.pem` y `key.pem` autofirmados,
sirve la web en HTTPS por el puerto 8000 y habilita el WebSocket seguro:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Remove-Item Env:AUDIT_LOCAL_HTTP -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe api_auditoria.py
```

Abre `https://<IP-local-de-la-PC>:8000` en el celular. El navegador mostrará
una advertencia porque el certificado es autofirmado; acepta el certificado
para habilitar cámara y WebSocket seguro. El celular y la PC deben estar en la
misma red, y el firewall debe permitir el puerto 8000. Para agregar una IP
adicional al certificado, define `AUDIT_CERT_IPS` con una lista separada por
comas antes de iniciar el servicio.
La página de XAMPP servida como `http://<IP-local>/...` no puede activar la
cámara en vivo; abre la dirección HTTPS del servicio en el puerto 8000.

Para pruebas solo en la PC, se puede usar HTTP en `localhost` (que el navegador
considera un origen seguro para la cámara) sin certificado:

```powershell
$env:AUDIT_LOCAL_HTTP = "1"
.\.venv\Scripts\python.exe api_auditoria.py
```

Abre `http://localhost:8000`. Este modo solo escucha en `127.0.0.1`; no está
disponible desde el celular ni desde otros equipos. Cierra la terminal o ejecuta
`Remove-Item Env:AUDIT_LOCAL_HTTP` para volver al modo HTTPS de red.

## Instalar la web como aplicación

En un navegador compatible, pulsa **Instalar app** en la portada y confirma la
instalación. En iPhone o iPad, abre la web en Safari, pulsa **Compartir** y elige
**Añadir a pantalla de inicio**. La instalación requiere HTTPS, excepto en
`localhost`. El manifiesto y el service worker usan rutas relativas para
funcionar tanto desde la raíz como desde la carpeta `frontend/` de XAMPP.

## Separación obligatoria de datos

Las fotos de visión deben dividirse por escena o sesión. No dividir archivos casi
idénticos al azar, porque produciría fuga de datos entre entrenamiento y prueba.

Los ejemplos del asistente deben identificarse explícitamente como sintéticos.
