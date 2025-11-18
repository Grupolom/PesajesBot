# 🚛 Módulo de Conductores - Sistema de Pesajes

## Descripción
Este módulo maneja el registro completo de pesajes para conductores, incluyendo validaciones específicas según el tipo de carga y alertas automáticas.

## Flujo del Bot

### 1️⃣ Cédula del Conductor
- Validación: Solo números
- Pregunta: "¿Cuál es su cédula?"

### 2️⃣ Placa del Camión
- Validación: 3 letras + 3 números (ejemplo: NHU982)
- Pregunta: "¿Cuál es la placa del camión?"

### 3️⃣ Tipo de Transporte
Opciones disponibles:
- **Lechones**
- **Concentrado**
- **Cerdos Gordos**
- **Combustible**

### 4️⃣ Detalles según Tipo de Carga

#### 🐷 Para Lechones o Cerdos Gordos:
- Pregunta: "¿Cuántos animales va a transportar?"
- Validación: Número entero entre 1 y 5000

#### ⛽ Para Combustible:
- Pregunta: "¿Qué tipo de combustible?" (Diesel o Corriente)
- Pregunta: "¿Cuántos galones va a transportar?"
- Validación: Número positivo (acepta decimales)

#### 📦 Para Concentrado:
- Pregunta: "Dato 1 de la factura"
- Pregunta: "Dato 2 de la factura"
- Pregunta: "Dato 3 de la factura"
- Pregunta: "Envíe foto de la factura"

### 5️⃣ Selección de Báscula

#### Restricciones por tipo de carga:
- **Báscula Italcol**: Solo disponible para Concentrado
- **Bogotá**: Solo disponible para Cerdos Gordos
- **Finca Tranquera**: Disponible para todos

### 6️⃣ Flujo Especial - Báscula Bogotá

Si se selecciona Bogotá (solo para Cerdos Gordos):

1. **Cerdos Vivos**
   - Pregunta: "¿Cuántos cerdos llegan VIVOS?"
   - Validación: Número entero 0-5000

2. **Cerdos Muertos**
   - Pregunta: "¿Hay cerdos MUERTOS?"
   - Validación: Número entero 0-1000
   - **ALERTA ESPECIAL**: Si hay cerdos muertos (>0), se muestra una alerta visual con emojis rojos

3. **Notificación al Grupo**
   - Se envía alerta al grupo de Telegram
   - Mensaje en MAYÚSCULAS con símbolos rojos
   - Ejemplo: "🚨 ¡¡¡ALERTA CRÍTICA!!! 🚨 SE MURIERON X CERDOS"

### 7️⃣ Peso del Pesaje
- Pregunta: "¿Cuánto pesa? (en kilogramos)"
- Validación: Número positivo, acepta decimales con coma o punto
- Rango: 0.01 - 100,000 kg

### 8️⃣ Foto del Pesaje
- Pregunta: "Envíe una foto del pesaje"
- Validación: Debe ser una imagen (no texto)
- Se sube automáticamente a Google Drive

### 9️⃣ Confirmación
- Muestra resumen completo de todos los datos
- Pregunta: "¿Está seguro de este peso y la información?"
- Opciones: ✅ Sí, confirmar / ❌ No, cancelar

### 🔟 Finalización
- Guarda en base de datos (tabla `conductores`)
- Envía notificación al grupo de Telegram con resumen completo
- Vuelve al menú principal

## Base de Datos

### Tabla: `conductores`

```sql
CREATE TABLE conductores (
    id SERIAL PRIMARY KEY,
    cedula VARCHAR(20) NOT NULL,
    placa VARCHAR(10) NOT NULL,
    tipo_carga VARCHAR(50) NOT NULL,
    num_animales INTEGER,
    tipo_combustible VARCHAR(20),
    cantidad_galones DECIMAL(10, 2),
    factura_dato1 VARCHAR(200),
    factura_dato2 VARCHAR(200),
    factura_dato3 VARCHAR(200),
    factura_foto TEXT,
    bascula VARCHAR(50) NOT NULL,
    cerdos_vivos INTEGER,
    cerdos_muertos INTEGER,
    peso DECIMAL(10, 2) NOT NULL,
    foto_pesaje TEXT,
    fecha TIMESTAMP DEFAULT NOW()
)
```

## Funciones de Validación

### `validar_placa_conductor(valor: str) -> bool`
Valida formato de placa: 3 letras + 3 números

### `validar_numero_entero(valor: str, minimo: int, maximo: int) -> tuple`
Retorna: (es_valido, numero, mensaje_error)

### `validar_galones(valor: str) -> tuple`
Valida cantidad de galones (acepta decimales)
Retorna: (es_valido, cantidad, mensaje_error)

## Notificaciones al Grupo

El sistema envía notificaciones automáticas al grupo de Telegram configurado con:

- 📅 Fecha y hora
- 👤 Cédula del conductor
- 🚛 Placa del camión
- 📦 Tipo de carga y detalles específicos
- 🏢 Báscula utilizada
- ⚖️ Peso registrado
- 📸 Enlaces a fotos (Google Drive)

### Alertas Especiales

**Para cerdos muertos en Bogotá:**
```
🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴
🚨 ¡¡¡ALERTA CRÍTICA!!! 🚨
⚠️ SE MURIERON X CERDOS ⚠️
🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴
```

## Estados FSM

Todos los estados están definidos en la clase `ConductoresState`:

- `cedula` - Captura de cédula
- `placa` - Captura de placa
- `tipo_transporte` - Selección de tipo de carga
- `num_animales` - Cantidad de animales
- `tipo_combustible` - Tipo de combustible
- `cantidad_galones` - Cantidad de galones
- `factura_dato1`, `factura_dato2`, `factura_dato3` - Datos de factura
- `factura_foto` - Foto de factura
- `bascula` - Selección de báscula
- `peso` - Registro de peso
- `foto_pesaje` - Foto del pesaje
- `confirmar_peso` - Confirmación final
- `cerdos_vivos` - Cantidad de cerdos vivos (Bogotá)
- `cerdos_muertos` - Cantidad de cerdos muertos (Bogotá)

## Ejemplo de Uso

1. Usuario escribe `/start`
2. Selecciona "3️⃣ Conductores"
3. Ingresa cédula: `1234567890`
4. Ingresa placa: `NHU982`
5. Selecciona "3. Cerdos Gordos"
6. Ingresa cantidad: `150`
7. Selecciona "2. Bogotá"
8. Ingresa cerdos vivos: `148`
9. Ingresa cerdos muertos: `2` ⚠️ **SE GENERA ALERTA**
10. Ingresa peso: `15000`
11. Envía foto del pesaje
12. Confirma la información
13. ✅ Registro completado

## Estructura de Archivos

- `main.py` - Contiene todo el flujo de Conductores
- `imagenes_pesajes/` - Carpeta donde se guardan las fotos localmente
- Google Drive - Almacenamiento en la nube de las fotos

## Variables de Entorno Necesarias

```env
BOT_TOKEN=tu_token_de_telegram
DATABASE_URL=postgresql://...
GROUP_CHAT_ID=id_del_grupo
GOOGLE_FOLDER_ID=id_carpeta_drive
GOOGLE_CREDENTIALS_PATH=ruta/credenciales.json
```

## Características Especiales

✅ Validaciones estrictas según tipo de carga
✅ Restricciones de báscula por tipo de transporte
✅ Alertas visuales para situaciones críticas
✅ Subida automática de fotos a Google Drive
✅ Notificaciones al grupo con formato profesional
✅ Confirmación antes de guardar
✅ Manejo de errores robusto
✅ Cancelación en cualquier momento con "0"

## Desarrollado por
Samuel - Rama: `feature/conductores`

---

**Nota**: Este módulo está completamente separado del flujo de Operario Sitio 3 y Operario Sitio 1, permitiendo trabajo en paralelo sin conflictos.
