import os
import re
import asyncio
import asyncpg
import uuid
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardBuilder
from aiogram.types import ReplyKeyboardRemove
from dotenv import load_dotenv
from datetime import datetime

# Librerías para Google Drive
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

# ==================== CARGAR VARIABLES DE ENTORNO ==================== #
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH")

# Validar BOT_TOKEN (obligatorio)
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN no está definido en el archivo .env")
    print("Por favor agrega: BOT_TOKEN=tu_token_aqui")
    raise SystemExit(1)

# Convertir GROUP_CHAT_ID a entero si es posible
if GROUP_CHAT_ID:
    try:
        GROUP_CHAT_ID = int(GROUP_CHAT_ID)
    except ValueError:
        print(f"⚠️ WARNING: GROUP_CHAT_ID no es numérico: {GROUP_CHAT_ID}")
        print("Se usará como string (para canales públicos con @)")
else:
    print("⚠️ WARNING: GROUP_CHAT_ID no está definido. No se enviarán notificaciones al grupo.")

# ==================== CONFIGURAR GOOGLE DRIVE ==================== #
def upload_to_drive(file_path, file_name):
    """Sube un archivo a Google Drive y retorna el link público"""
    if not GOOGLE_CREDENTIALS_PATH:
        print("⚠️ GOOGLE_CREDENTIALS_PATH no está configurado en .env")
        return None
    
    if not os.path.exists(GOOGLE_CREDENTIALS_PATH):
        print(f"⚠️ Archivo de credenciales no encontrado: {GOOGLE_CREDENTIALS_PATH}")
        return None
    
    if not GOOGLE_FOLDER_ID:
        print("⚠️ GOOGLE_FOLDER_ID no está configurado en .env")
        return None
    
    try:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_PATH,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)
        
        file_metadata = {
            'name': file_name,
            'parents': [GOOGLE_FOLDER_ID]
        }
        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,webViewLink',
            supportsAllDrives=True
        ).execute()
        
        file_id = file.get('id')
        
        # Hacer el archivo público
        try:
            permission = {
                'type': 'anyone',
                'role': 'reader'
            }
            service.permissions().create(
                fileId=file_id,
                body=permission,
                supportsAllDrives=True
            ).execute()
        except Exception as perm_error:
            print(f"⚠️ No se pudo hacer el archivo público: {perm_error}")
        
        link = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        print(f"✅ Imagen subida a Drive: {link}")
        return link
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error completo subiendo a Drive:")
        print(f"   {error_msg}")
        
        if "storageQuotaExceeded" in error_msg or "storage quota" in error_msg.lower():
            print(f"   💡 Solución: Comparte la carpeta con: pesajes-bot@pesajesbot.iam.gserviceaccount.com")
        elif "404" in error_msg or "not found" in error_msg.lower():
            print(f"   💡 La carpeta con ID {GOOGLE_FOLDER_ID} no existe o no es accesible")
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            print(f"   💡 La Service Account no tiene permisos de Editor en la carpeta")
        
        return None

# ==================== CONEXIÓN BASE DE DATOS ==================== #
# Pool de conexiones global
db_pool = None

async def init_db_pool():
    """Inicializa el pool de conexiones a PostgreSQL"""
    global db_pool
    if not DATABASE_URL:
        print("⚠️ DATABASE_URL no está configurado. No se usará base de datos.")
        return None
    
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=60,
            max_inactive_connection_lifetime=300  # 5 minutos
        )
        print("✅ Pool de conexiones a PostgreSQL creado correctamente")
        return db_pool
    except Exception as e:
        print(f"❌ Error creando pool de PostgreSQL: {e}")
        return None

async def get_db_connection():
    """Obtiene una conexión del pool, reconectando si es necesario"""
    global db_pool
    
    # Si no hay pool, intentar crear uno
    if db_pool is None:
        print("⚠️ Pool no existe, intentando crear...")
        await init_db_pool()
    
    # Si aún no hay pool, retornar None
    if db_pool is None:
        return None
    
    try:
        # Intentar obtener una conexión
        conn = await db_pool.acquire()
        return conn
    except Exception as e:
        print(f"❌ Error obteniendo conexión: {e}")
        print("🔄 Intentando recrear el pool...")
        
        # Cerrar pool antiguo si existe
        try:
            if db_pool:
                await db_pool.close()
        except:
            pass
        
        db_pool = None
        
        # Intentar crear nuevo pool
        await init_db_pool()
        
        if db_pool:
            try:
                conn = await db_pool.acquire()
                return conn
            except:
                return None
        
        return None

async def release_db_connection(conn):
    """Libera una conexión de vuelta al pool"""
    global db_pool
    if conn and db_pool:
        try:
            await db_pool.release(conn)
        except Exception as e:
            print(f"⚠️ Error liberando conexión: {e}")

# ==================== ESTADOS FSM ==================== #
class RegistroState(StatesGroup):
    # Menú principal (multi-perfil)
    menu_principal = State()  # Menú inicial con 3 opciones

    # Estados antiguos (Conductores - Sistema de Pesajes)
    cedula = State()
    confirmar_cedula = State()
    tipo_empleado = State()  # NUEVO: Tipo de empleado
    confirmar_tipo_empleado = State()  # NUEVO: Confirmar tipo de empleado
    camion = State()
    confirmar_camion = State()
    tipo_carga = State()  # NUEVO: Tipo de carga
    especificar_otros = State()  # NUEVO: Especificar si selecciona "Otros"
    confirmar_tipo_carga = State()  # NUEVO: Confirmar tipo de carga
    tipo = State()
    confirmar_tipo = State()
    peso_origen = State()
    confirmar_peso_origen = State()
    peso_bascula_destino = State()
    confirmar_peso_bascula = State()
    silo_num = State()
    silo_peso = State()
    confirmar_silo_peso = State()  # Confirmar peso de silo
    foto = State()
    consulta_silo = State()  # Para consultar capacidad de silos
    restar_silo = State()  # Para restar peso de silos
    restar_silo_numero = State()
    restar_silo_peso = State()
    confirmar_restar_peso = State()  # Confirmar peso a restar

    # ==================== NUEVOS ESTADOS: OPERARIO SITIO 3 ==================== #
    sitio3_menu = State()  # Submenú Operario Sitio 3

    # Estados para Registro de Animales
    sitio3_cedula = State()
    sitio3_confirmar_cedula = State()
    sitio3_cantidad_animales = State()
    sitio3_confirmar_cantidad = State()
    sitio3_rango_corrales = State()
    sitio3_confirmar_rango = State()
    sitio3_tipo_comida = State()
    sitio3_confirmar_tipo_comida = State()
    sitio3_agregar_mas = State()

    # Estados para Descarga de Animales
    descarga_cedula = State()
    descarga_confirmar_cedula = State()
    descarga_cantidad_lechones = State()
    descarga_confirmar_cantidad = State()
    descarga_rango_corrales = State()
    descarga_confirmar_rango = State()
    descarga_numero_lote = State()
    descarga_confirmar_lote = State()

# ==================== ESTADOS PARA MENU CONDUCTORES ==================== #
class ConductoresState(StatesGroup):
    """Estados separados para el menú de conductores"""
    menu_conductores = State()
    
    # Flujo de registro de pesaje conductores
    cedula = State()
    confirmar_cedula = State()
    
    placa = State()
    confirmar_placa = State()
    
    tipo_transporte = State()
    confirmar_tipo_transporte = State()
    
    # Estados específicos para cada tipo de carga
    num_animales = State()
    confirmar_num_animales = State()
    
    tipo_combustible = State()
    confirmar_tipo_combustible = State()
    
    cantidad_galones = State()
    confirmar_cantidad_galones = State()
    
    numero_factura = State()
    confirmar_numero_factura = State()
    
    tipo_alimento = State()
    confirmar_tipo_alimento = State()
    
    kilos_comprados = State()
    confirmar_kilos_comprados = State()
    
    factura_foto = State()
    
    # Selección de báscula
    bascula = State()
    confirmar_bascula = State()
    
    # Registro de peso
    peso = State()
    confirmar_peso_input = State()
    
    foto_pesaje = State()
    confirmar_peso = State()
    
    # Flujo especial para báscula Bogotá (solo cerdos gordos)
    cerdos_vivos = State()
    confirmar_cerdos_vivos = State()
    
    cerdos_muertos = State()
    confirmar_cerdos_muertos = State()

# ==================== VALIDACIONES ==================== #
def validar_cedula(valor):
    return valor.isdigit()

def validar_placa(valor):
    return re.fullmatch(r"^[A-Z]{3}\d{3}$", valor.upper())

def validar_placa_conductor(valor: str) -> bool:
    """Valida placa de camión: 3 letras mayúsculas + 3 números (ej: NHU982)"""
    return re.fullmatch(r"^[A-Z]{3}\d{3}$", valor.upper()) is not None

def validar_numero_entero(valor: str, minimo: int = 1, maximo: int = 10000) -> tuple[bool, int, str]:
    """
    Valida número entero positivo dentro de un rango
    Retorna: (es_valido, numero, mensaje_error)
    """
    try:
        numero = int(valor)
        if numero < minimo:
            return False, 0, f"El número debe ser al menos {minimo}"
        if numero > maximo:
            return False, 0, f"El número no puede superar {maximo}"
        return True, numero, ""
    except ValueError:
        return False, 0, "Debe ingresar un número entero válido"

def validar_galones(valor: str) -> tuple[bool, float, str]:
    """
    Valida cantidad de galones: número positivo, puede tener decimales
    Retorna: (es_valido, cantidad, mensaje_error)
    """
    try:
        # Reemplazar coma por punto para decimales
        valor_limpio = valor.replace(",", ".")
        galones = float(valor_limpio)
        
        if galones <= 0:
            return False, 0, "La cantidad debe ser mayor a 0"
        if galones > 100000:
            return False, 0, "La cantidad no puede superar 100,000 galones"
        
        return True, galones, ""
    except ValueError:
        return False, 0, "Debe ingresar un número válido (puede usar decimales con coma o punto)"

def validar_peso(valor):
    return re.fullmatch(r"^\d+(,\d+)?$", valor)

# ==================== VALIDACIONES OPERARIO SITIO 3 ==================== #
def validar_cedula_sitio3(valor: str) -> bool:
    """Valida cédula para Sitio 3: solo números, 6-12 dígitos"""
    if not valor.isdigit():
        return False
    if len(valor) < 6 or len(valor) > 12:
        return False
    return True

def validar_cantidad_animales(valor: str) -> tuple[bool, int, str]:
    """
    Valida cantidad de animales: entero positivo, 1-2000
    Retorna: (es_valido, cantidad, mensaje_error)
    """
    try:
        cantidad = int(valor)
        if cantidad < 1:
            return False, 0, "La cantidad debe ser al menos 1 animal"
        if cantidad > 2000:
            return False, 0, "La cantidad no puede superar 2000 animales"
        return True, cantidad, ""
    except ValueError:
        return False, 0, "Debe ingresar un número entero válido"

def validar_rango_corrales(valor: str) -> tuple[bool, str]:
    """
    Valida rango de corrales: formato X-Y donde X <= Y
    Retorna: (es_valido, mensaje_error)
    """
    # Validar formato con regex
    if not re.match(r'^\d+-\d+$', valor):
        return False, "Formato incorrecto. Use: número-número (ejemplo: 0-10)"

    # Extraer números
    partes = valor.split('-')
    try:
        inicio = int(partes[0])
        fin = int(partes[1])

        if inicio < 0 or fin < 0:
            return False, "Los números de corral no pueden ser negativos"

        if inicio > fin:
            return False, f"El número inicial ({inicio}) debe ser menor o igual al final ({fin})"

        return True, ""
    except ValueError:
        return False, "Error al procesar los números"

# ==================== VALIDACIONES DESCARGA DE ANIMALES ==================== #
def validar_cantidad_lechones(valor: str) -> tuple[bool, int, str]:
    """
    Valida cantidad de lechones: entero positivo, 1-5000
    Retorna: (es_valido, cantidad, mensaje_error)
    """
    try:
        cantidad = int(valor)
        if cantidad < 1:
            return False, 0, "La cantidad debe ser al menos 1 lechón"
        if cantidad > 5000:
            return False, 0, "La cantidad no puede superar 5000 lechones (límite de capacidad)"
        return True, cantidad, ""
    except ValueError:
        return False, 0, "Debe ingresar un número entero válido"

def validar_numero_lote(valor: str) -> tuple[bool, str]:
    """
    Valida número de lote: alfanumérico, 3-30 caracteres
    Permite: letras, números, guiones, guiones bajos
    Retorna: (es_valido, mensaje_error)
    """
    # Validar formato con regex
    if not re.match(r'^[A-Za-z0-9_-]{3,30}$', valor):
        if len(valor) < 3:
            return False, "El número de lote es muy corto (mínimo 3 caracteres)"
        elif len(valor) > 30:
            return False, "El número de lote es muy largo (máximo 30 caracteres)"
        elif ' ' in valor:
            return False, "El número de lote no puede contener espacios"
        else:
            return False, "El número de lote solo puede contener letras, números, guiones (-) y guiones bajos (_)"

    return True, ""

async def volver_menu_principal(message: types.Message, state: FSMContext):
    """Función helper para volver al menú principal multi-perfil"""
    await state.clear()
    await message.answer(
        "👋 *Bienvenido al Sistema de Gestión*\n\n"
        "Seleccione su perfil:\n\n"
        "1️⃣ Operario Sitio 3\n"
        "2️⃣ Operario Sitio 1\n"
        "3️⃣ Conductores\n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 en cualquier momento para cancelar_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.menu_principal)

async def volver_menu_sitio3(message: types.Message, state: FSMContext):
    """Función helper para volver al submenú de Operario Sitio 3"""
    await message.answer(
        "🐷 *OPERARIO SITIO 3*\n\n"
        "Seleccione una opción:\n\n"
        "1️⃣ Registro de Animales\n"
        "2️⃣ Medición de Silos _(Próximamente)_\n"
        "3️⃣ Descarga de Animales \n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 para volver al menú principal_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_menu)

# ==================== CONFIGURAR BOT ==================== #
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== HANDLER GLOBAL PARA CANCELAR ==================== #
@dp.message(F.text == "0")
async def cancelar_operacion(message: types.Message, state: FSMContext):
    """Permite al usuario cancelar en cualquier momento escribiendo 0"""
    current_state = await state.get_state()
    if current_state and current_state != RegistroState.menu_principal:
        await message.answer("❌ Operación cancelada.")
        await volver_menu_principal(message, state)

# ==================== FLUJO DE BOT ==================== #
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    """Handler inicial - Muestra menú principal multi-perfil"""
    await state.clear()
    await message.answer(
        "👋 *Bienvenido al Sistema de Gestión*\n\n"
        "Seleccione su perfil:\n\n"
        "1️⃣ Operario Sitio 3\n"
        "2️⃣ Operario Sitio 1\n"
        "3️⃣ Conductores\n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 en cualquier momento para cancelar_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.menu_principal)

# ==================== MENÚ PRINCIPAL MULTI-PERFIL ==================== #
@dp.message(RegistroState.menu_principal, F.text == "1")
async def menu_operario_sitio3(message: types.Message, state: FSMContext):
    """Opción 1: Menú Operario Sitio 3"""
    await volver_menu_sitio3(message, state)

@dp.message(RegistroState.menu_principal, F.text == "2")
async def menu_operario_sitio1(message: types.Message, state: FSMContext):
    """Opción 2: Operario Sitio 1 (Placeholder)"""
    await message.answer(
        "🚧 *OPERARIO SITIO 1*\n\n"
        "Esta funcionalidad estará disponible próximamente.\n\n"
        "Volviendo al menú principal...",
        parse_mode="Markdown"
    )
    await volver_menu_principal(message, state)

@dp.message(RegistroState.menu_principal, F.text == "3")
async def menu_conductores(message: types.Message, state: FSMContext):
    """Opción 3: Conductores - Nuevo flujo de pesajes"""
    await state.clear()
    # Guardar telegram_id automáticamente
    await state.update_data(telegram_id=message.from_user.id)
    await message.answer(
        "🚛 *CONDUCTORES - REGISTRO DE PESAJE*\n\n"
        "Por favor, ingrese su *cédula*:",
        parse_mode="Markdown"
    )
    await state.set_state(ConductoresState.cedula)

# ==================== NUEVO FLUJO CONDUCTORES ==================== #

# Función helper para confirmaciones
async def preguntar_confirmacion(message: types.Message, valor: str, campo: str):
    """Pregunta si el valor ingresado es correcto"""
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text="1. Confirmar")
    keyboard.button(text="2. Modificar")
    keyboard.adjust(2)
    
    await message.answer(
        f"Usted ingresó: *{valor}*\n\n"
        f"¿Está seguro que está correctamente escrito?\n\n"
        f"1️⃣ Confirmar\n"
        f"2️⃣ Modificar",
        reply_markup=keyboard.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )

# 1. CÉDULA
@dp.message(ConductoresState.cedula)
async def procesar_cedula_conductor(message: types.Message, state: FSMContext):
    """Recibe y valida la cédula del conductor"""
    cedula = message.text.strip()
    
    if not validar_cedula(cedula):
        await message.answer("⚠️ Cédula inválida. Debe contener solo números.\n\nIntente nuevamente:")
        return
    
    await state.update_data(cedula_temp=cedula)
    await preguntar_confirmacion(message, cedula, "cédula")
    await state.set_state(ConductoresState.confirmar_cedula)

@dp.message(ConductoresState.confirmar_cedula)
async def confirmar_cedula_conductor(message: types.Message, state: FSMContext):
    """Confirma la cédula o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "Por favor, ingrese nuevamente su *cédula*:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.cedula)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        cedula = data.get("cedula_temp")
        await state.update_data(cedula=cedula)
        
        await message.answer(
            f"✅ Cédula: *{cedula}*\n\n"
            f"Ahora, ingrese la *placa del camión*:\n"
            f"_(Formato: 3 letras + 3 números, ejemplo: NHU982)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.placa)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 2. PLACA
@dp.message(ConductoresState.placa)
async def procesar_placa_conductor(message: types.Message, state: FSMContext):
    """Recibe y valida la placa del camión"""
    placa = message.text.strip().upper()
    
    if not validar_placa_conductor(placa):
        await message.answer(
            "⚠️ Placa inválida. Debe tener el formato: 3 letras + 3 números\n"
            "Ejemplo: NHU982\n\n"
            "Intente nuevamente:"
        )
        return
    
    await state.update_data(placa_temp=placa)
    await preguntar_confirmacion(message, placa, "placa")
    await state.set_state(ConductoresState.confirmar_placa)

@dp.message(ConductoresState.confirmar_placa)
async def confirmar_placa_conductor(message: types.Message, state: FSMContext):
    """Confirma la placa o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "Por favor, ingrese nuevamente la *placa del camión*:\n"
            "_(Formato: 3 letras + 3 números, ejemplo: NHU982)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.placa)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        placa = data.get("placa_temp")
        await state.update_data(placa=placa)
        
        # Crear teclado con opciones
        keyboard = ReplyKeyboardBuilder()
        keyboard.button(text="1. Lechones")
        keyboard.button(text="2. Concentrado")
        keyboard.button(text="3. Cerdos Gordos")
        keyboard.button(text="4. Combustible")
        keyboard.adjust(2, 2)
        
        await message.answer(
            f"✅ Placa: *{placa}*\n\n"
            f"¿Qué va a transportar?\n\n"
            f"1️⃣ Lechones (cerdos pequeños)\n"
            f"2️⃣ Concentrado (alimento)\n"
            f"3️⃣ Cerdos Gordos (para venta)\n"
            f"4️⃣ Combustible (diesel/corriente)\n\n"
            f"Seleccione una opción:",
            reply_markup=keyboard.as_markup(resize_keyboard=True),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.tipo_transporte)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 3. TIPO DE TRANSPORTE
@dp.message(ConductoresState.tipo_transporte)
async def procesar_tipo_transporte(message: types.Message, state: FSMContext):
    """Procesa el tipo de carga a transportar"""
    texto = message.text.strip().lower()
    
    # Mapear la entrada del usuario
    tipo_carga = None
    if "1" in texto or "lechon" in texto:
        tipo_carga = "Lechones"
    elif "2" in texto or "concentrado" in texto:
        tipo_carga = "Concentrado"
    elif "3" in texto or "cerdo" in texto or "gordo" in texto:
        tipo_carga = "Cerdos Gordos"
    elif "4" in texto or "combustible" in texto:
        tipo_carga = "Combustible"
    else:
        await message.answer("⚠️ Opción no válida. Por favor seleccione una de las opciones del menú.")
        return
    
    await state.update_data(tipo_carga_temp=tipo_carga)
    await preguntar_confirmacion(message, tipo_carga, "tipo de transporte")
    await state.set_state(ConductoresState.confirmar_tipo_transporte)

@dp.message(ConductoresState.confirmar_tipo_transporte)
async def confirmar_tipo_transporte(message: types.Message, state: FSMContext):
    """Confirma el tipo de transporte o permite modificarlo"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        # Volver a mostrar opciones
        keyboard = ReplyKeyboardBuilder()
        keyboard.button(text="1. Lechones")
        keyboard.button(text="2. Concentrado")
        keyboard.button(text="3. Cerdos Gordos")
        keyboard.button(text="4. Combustible")
        keyboard.adjust(2, 2)
        
        await message.answer(
            "¿Qué va a transportar?\n\n"
            "1️⃣ Lechones (cerdos pequeños)\n"
            "2️⃣ Concentrado (alimento)\n"
            "3️⃣ Cerdos Gordos (para venta)\n"
            "4️⃣ Combustible (diesel/corriente)\n\n"
            "Seleccione una opción:",
            reply_markup=keyboard.as_markup(resize_keyboard=True)
        )
        await state.set_state(ConductoresState.tipo_transporte)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        tipo_carga = data.get("tipo_carga_temp")
        await state.update_data(tipo_carga=tipo_carga)
    
        # Dependiendo del tipo de carga, hacer diferentes preguntas
        if tipo_carga == "Lechones" or tipo_carga == "Cerdos Gordos":
            animal_tipo = "lechones" if tipo_carga == "Lechones" else "cerdos gordos"
            await message.answer(
                f"✅ Tipo de carga: *{tipo_carga}*\n\n"
                f"¿Cuántos {animal_tipo} va a transportar?\n"
                f"_(Ingrese solo el número)_",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            await state.set_state(ConductoresState.num_animales)
        
        elif tipo_carga == "Combustible":
            keyboard = ReplyKeyboardBuilder()
            keyboard.button(text="Diesel")
            keyboard.button(text="Corriente")
            keyboard.adjust(2)
            
            await message.answer(
                f"✅ Tipo de carga: *{tipo_carga}*\n\n"
                f"¿Qué tipo de combustible?\n\n"
                f"Seleccione una opción:",
                reply_markup=keyboard.as_markup(resize_keyboard=True),
                parse_mode="Markdown"
            )
            await state.set_state(ConductoresState.tipo_combustible)
        
        elif tipo_carga == "Concentrado":
            await message.answer(
                f"✅ Tipo de carga: *{tipo_carga}*\n\n"
                f"📋 *DATOS DE LA FACTURA*\n\n"
                f"Por favor ingrese el *número de factura*:",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            await state.set_state(ConductoresState.numero_factura)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 4a. NÚMERO DE ANIMALES (para Lechones o Cerdos Gordos)
@dp.message(ConductoresState.num_animales)
async def procesar_num_animales(message: types.Message, state: FSMContext):
    """Procesa el número de animales"""
    es_valido, cantidad, error = validar_numero_entero(message.text.strip(), minimo=1, maximo=5000)
    
    if not es_valido:
        await message.answer(f"⚠️ {error}\n\nIntente nuevamente:")
        return
    
    await state.update_data(num_animales_temp=cantidad)
    await preguntar_confirmacion(message, str(cantidad), "cantidad de animales")
    await state.set_state(ConductoresState.confirmar_num_animales)

@dp.message(ConductoresState.confirmar_num_animales)
async def confirmar_num_animales(message: types.Message, state: FSMContext):
    """Confirma la cantidad de animales o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        data = await state.get_data()
        tipo_carga = data.get("tipo_carga")
        animal_tipo = "lechones" if tipo_carga == "Lechones" else "cerdos gordos"
        
        await message.answer(
            f"¿Cuántos {animal_tipo} va a transportar?\n"
            f"_(Ingrese solo el número)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.num_animales)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        cantidad = data.get("num_animales_temp")
        await state.update_data(num_animales=cantidad)
        
        # Continuar al siguiente paso: selección de báscula
        await preguntar_bascula(message, state)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 4b. TIPO DE COMBUSTIBLE
@dp.message(ConductoresState.tipo_combustible)
async def procesar_tipo_combustible(message: types.Message, state: FSMContext):
    """Procesa el tipo de combustible"""
    tipo = message.text.strip().title()
    
    if tipo not in ["Diesel", "Corriente"]:
        await message.answer("⚠️ Opción no válida. Seleccione Diesel o Corriente:")
        return
    
    await state.update_data(tipo_combustible_temp=tipo)
    await preguntar_confirmacion(message, tipo, "tipo de combustible")
    await state.set_state(ConductoresState.confirmar_tipo_combustible)

@dp.message(ConductoresState.confirmar_tipo_combustible)
async def confirmar_tipo_combustible(message: types.Message, state: FSMContext):
    """Confirma el tipo de combustible o permite modificarlo"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        keyboard = ReplyKeyboardBuilder()
        keyboard.button(text="Diesel")
        keyboard.button(text="Corriente")
        keyboard.adjust(2)
        
        await message.answer(
            "¿Qué tipo de combustible?\n\n"
            "Seleccione una opción:",
            reply_markup=keyboard.as_markup(resize_keyboard=True)
        )
        await state.set_state(ConductoresState.tipo_combustible)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        tipo = data.get("tipo_combustible_temp")
        await state.update_data(tipo_combustible=tipo)
        
        await message.answer(
            f"✅ Tipo de combustible: *{tipo}*\n\n"
            f"¿Cuántos galones va a transportar?\n"
            f"_(Puede usar decimales con coma o punto)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.cantidad_galones)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 4c. CANTIDAD DE GALONES
@dp.message(ConductoresState.cantidad_galones)
async def procesar_cantidad_galones(message: types.Message, state: FSMContext):
    """Procesa la cantidad de galones"""
    es_valido, galones, error = validar_galones(message.text.strip())
    
    if not es_valido:
        await message.answer(f"⚠️ {error}\n\nIntente nuevamente:")
        return
    
    await state.update_data(cantidad_galones_temp=galones)
    await preguntar_confirmacion(message, f"{galones:,.2f} galones", "cantidad")
    await state.set_state(ConductoresState.confirmar_cantidad_galones)

@dp.message(ConductoresState.confirmar_cantidad_galones)
async def confirmar_cantidad_galones(message: types.Message, state: FSMContext):
    """Confirma la cantidad de galones o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "¿Cuántos galones va a transportar?\n"
            "_(Puede usar decimales con coma o punto)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.cantidad_galones)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        galones = data.get("cantidad_galones_temp")
        await state.update_data(cantidad_galones=galones)
        
        # Continuar a selección de báscula
        await preguntar_bascula(message, state)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 4d. DATOS DE FACTURA (para Concentrado)
@dp.message(ConductoresState.numero_factura)
async def procesar_numero_factura(message: types.Message, state: FSMContext):
    """Procesa el número de factura"""
    numero = message.text.strip()
    await state.update_data(numero_factura_temp=numero)
    
    await preguntar_confirmacion(message, numero, "número de factura")
    await state.set_state(ConductoresState.confirmar_numero_factura)

@dp.message(ConductoresState.confirmar_numero_factura)
async def confirmar_numero_factura(message: types.Message, state: FSMContext):
    """Confirma el número de factura o permite modificarlo"""
    texto = message.text.strip().lower()
    print(f"DEBUG confirmar_numero_factura: texto='{texto}'")
    
    if "2" in texto or "modificar" in texto:
        print("DEBUG: Entrando a modificar")
        await message.answer(
            "✏️ Ingrese nuevamente el *número de factura*:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.numero_factura)
        return
    
    if "1" in texto or "confirmar" in texto:
        print("DEBUG: Entrando a confirmar")
        data = await state.get_data()
        numero = data.get("numero_factura_temp")
        print(f"DEBUG: numero={numero}")
        await state.update_data(numero_factura=numero)
        
        # Preguntar tipo de alimento
        keyboard = ReplyKeyboardBuilder()
        keyboard.button(text="1. Levante")
        keyboard.button(text="2. Engorde/Medicado")
        keyboard.button(text="3. Finalizador")
        keyboard.adjust(1)
        
        print("DEBUG: Enviando mensaje de tipo de alimento")
        await message.answer(
            f"✅ Número de factura: *{numero}*\n\n"
            f"📋 Seleccione el *tipo de alimento*:\n\n"
            f"1️⃣ *Levante*\n"
            f"2️⃣ *Engorde/Medicado*\n"
            f"3️⃣ *Finalizador*",
            reply_markup=keyboard.as_markup(resize_keyboard=True),
            parse_mode="Markdown"
        )
        print("DEBUG: Cambiando estado a tipo_alimento")
        await state.set_state(ConductoresState.tipo_alimento)
        print("DEBUG: Estado cambiado exitosamente")
        return
    
    print("DEBUG: Opción no válida")
    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

@dp.message(ConductoresState.tipo_alimento)
async def procesar_tipo_alimento(message: types.Message, state: FSMContext):
    """Procesa la selección del tipo de alimento"""
    texto = message.text.strip().lower()
    
    tipo = None
    if "1" in texto or "levante" in texto:
        tipo = "Levante"
    elif "2" in texto or "engorde" in texto or "medicado" in texto:
        tipo = "Engorde/Medicado"
    elif "3" in texto or "finalizador" in texto:
        tipo = "Finalizador"
    else:
        await message.answer(
            "⚠️ Opción no válida.\n\n"
            "Seleccione:\n"
            "1️⃣ Levante\n"
            "2️⃣ Engorde/Medicado\n"
            "3️⃣ Finalizador"
        )
        return
    
    await state.update_data(tipo_alimento_temp=tipo)
    await preguntar_confirmacion(message, tipo, "tipo de alimento")
    await state.set_state(ConductoresState.confirmar_tipo_alimento)

@dp.message(ConductoresState.confirmar_tipo_alimento)
async def confirmar_tipo_alimento(message: types.Message, state: FSMContext):
    """Confirma el tipo de alimento o permite modificarlo"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        keyboard = ReplyKeyboardBuilder()
        keyboard.button(text="1. Levante")
        keyboard.button(text="2. Engorde/Medicado")
        keyboard.button(text="3. Finalizador")
        keyboard.adjust(1)
        
        await message.answer(
            "✏️ Seleccione nuevamente el *tipo de alimento*:\n\n"
            f"1️⃣ *Levante*\n"
            f"2️⃣ *Engorde/Medicado*\n"
            f"3️⃣ *Finalizador*",
            reply_markup=keyboard.as_markup(resize_keyboard=True),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.tipo_alimento)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        tipo = data.get("tipo_alimento_temp")
        await state.update_data(tipo_alimento=tipo)
        
        await message.answer(
            f"✅ Tipo de alimento: *{tipo}*\n\n"
            f"📊 Ingrese los *kilos comprados* (número):",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.kilos_comprados)
        return
    
    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

@dp.message(ConductoresState.kilos_comprados)
async def procesar_kilos_comprados(message: types.Message, state: FSMContext):
    """Procesa los kilos comprados"""
    es_valido, kilos, error = validar_galones(message.text.strip())
    
    if not es_valido:
        await message.answer(f"⚠️ {error}\n\nIntente nuevamente:")
        return
    
    await state.update_data(kilos_comprados_temp=kilos)
    await preguntar_confirmacion(message, f"{kilos:,.2f} kg", "kilos comprados")
    await state.set_state(ConductoresState.confirmar_kilos_comprados)

@dp.message(ConductoresState.confirmar_kilos_comprados)
async def confirmar_kilos_comprados(message: types.Message, state: FSMContext):
    """Confirma los kilos comprados o permite modificarlos"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "✏️ Ingrese nuevamente los *kilos comprados*:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.kilos_comprados)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        kilos = data.get("kilos_comprados_temp")
        await state.update_data(kilos_comprados=kilos)
        
        await message.answer(
            f"✅ Kilos comprados: *{kilos:,.2f} kg*\n\n"
            f"📸 Ahora envíe una *foto de la factura*:",
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.factura_foto)
        return
    
    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

@dp.message(ConductoresState.factura_foto, F.photo)
async def procesar_factura_foto(message: types.Message, state: FSMContext):
    """Procesa la foto de la factura"""
    # Obtener la foto de mayor resolución
    photo = message.photo[-1]
    file_id = photo.file_id
    
    # Descargar foto
    file = await bot.get_file(file_id)
    os.makedirs("imagenes_pesajes", exist_ok=True)
    
    data = await state.get_data()
    cedula = data.get("cedula")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"factura_{cedula}_{timestamp}.jpg"
    file_path = os.path.join("imagenes_pesajes", filename)
    
    await bot.download_file(file.file_path, file_path)
    
    # Subir a Drive
    drive_link = upload_to_drive(file_path, filename)
    await state.update_data(factura_foto=drive_link or file_path)
    
    await message.answer(
        f"✅ Foto de factura recibida\n\n"
        f"Continuando con el registro..."
    )
    
    # Continuar a selección de báscula
    await preguntar_bascula(message, state)

@dp.message(ConductoresState.factura_foto)
async def factura_foto_no_valida(message: types.Message, state: FSMContext):
    """Handler para cuando no envían una foto"""
    await message.answer("⚠️ Por favor envíe una FOTO de la factura (no texto).")

# 5. SELECCIÓN DE BÁSCULA
async def preguntar_bascula(message: types.Message, state: FSMContext):
    """Pregunta qué báscula va a usar, con restricciones según tipo de carga"""
    data = await state.get_data()
    tipo_carga = data.get("tipo_carga")
    
    # Crear opciones de báscula según restricciones
    keyboard = ReplyKeyboardBuilder()
    opciones_texto = []
    
    # Báscula Italcol: solo para concentrado (ÚNICA OPCIÓN)
    if tipo_carga == "Concentrado":
        keyboard.button(text="1. Báscula Italcol")
        opciones_texto.append("1️⃣ Báscula Italcol")
    else:
        # Para otros tipos de carga
        # Báscula Bogotá: solo para cerdos gordos
        if tipo_carga == "Cerdos Gordos":
            keyboard.button(text="2. Bogotá")
            opciones_texto.append("2️⃣ Bogotá")
        
        # Finca Tranquera: disponible para todos excepto concentrado
        keyboard.button(text="3. Finca Tranquera")
        opciones_texto.append("3️⃣ Finca Tranquera")
    
    keyboard.adjust(1)  # Una opción por fila
    
    opciones_str = "\n".join(opciones_texto)
    
    await message.answer(
        f"🏢 ¿Qué báscula vas a registrar para el pesaje?\n\n"
        f"{opciones_str}\n\n"
        f"Seleccione una opción:",
        reply_markup=keyboard.as_markup(resize_keyboard=True)
    )
    await state.set_state(ConductoresState.bascula)

@dp.message(ConductoresState.bascula)
async def procesar_bascula(message: types.Message, state: FSMContext):
    """Procesa la selección de báscula"""
    texto = message.text.strip().lower()
    data = await state.get_data()
    tipo_carga = data.get("tipo_carga")
    
    bascula = None
    if "1" in texto or "italcol" in texto:
        if tipo_carga == "Concentrado":
            bascula = "Báscula Italcol"
        else:
            await message.answer("⚠️ La Báscula Italcol solo está disponible para Concentrado.")
            return
    elif "2" in texto or "bogota" in texto or "bogotá" in texto:
        if tipo_carga == "Cerdos Gordos":
            bascula = "Bogotá"
        else:
            await message.answer("⚠️ Bogotá solo está disponible para Cerdos Gordos.")
            return
    elif "3" in texto or "finca" in texto or "tranquera" in texto:
        bascula = "Finca Tranquera"
    else:
        await message.answer("⚠️ Opción no válida. Seleccione una de las opciones disponibles.")
        return
    
    await state.update_data(bascula_temp=bascula)
    await preguntar_confirmacion(message, bascula, "báscula")
    await state.set_state(ConductoresState.confirmar_bascula)

@dp.message(ConductoresState.confirmar_bascula)
async def confirmar_bascula(message: types.Message, state: FSMContext):
    """Confirma la báscula o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        # Volver a preguntar báscula
        await preguntar_bascula(message, state)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        bascula = data.get("bascula_temp")
        await state.update_data(bascula=bascula)
        
        # Si es Bogotá, hacer pregunta especial sobre cerdos vivos
        if bascula == "Bogotá":
            await message.answer(
                f"✅ Báscula: *{bascula}*\n\n"
                f"¿Cuántos cerdos llegan *VIVOS*?\n"
                f"_(Ingrese solo el número)_",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            await state.set_state(ConductoresState.cerdos_vivos)
        else:
            # Continuar con peso normal
            await message.answer(
                f"✅ Báscula: *{bascula}*\n\n"
                f"¿Cuánto pesa? _(en kilogramos)_\n"
                f"_(Puede usar decimales con coma)_",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            await state.set_state(ConductoresState.peso)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 6. FLUJO ESPECIAL BOGOTÁ - Cerdos vivos
@dp.message(ConductoresState.cerdos_vivos)
async def procesar_cerdos_vivos(message: types.Message, state: FSMContext):
    """Procesa cantidad de cerdos vivos y calcula automáticamente los muertos"""
    es_valido, cantidad_vivos, error = validar_numero_entero(message.text.strip(), minimo=0, maximo=5000)
    
    if not es_valido:
        await message.answer(f"⚠️ {error}\n\nIntente nuevamente:")
        return
    
    await state.update_data(cerdos_vivos_temp=cantidad_vivos)
    await preguntar_confirmacion(message, str(cantidad_vivos), "cantidad de cerdos vivos")
    await state.set_state(ConductoresState.confirmar_cerdos_vivos)

@dp.message(ConductoresState.confirmar_cerdos_vivos)
async def confirmar_cerdos_vivos(message: types.Message, state: FSMContext):
    """Confirma cantidad de cerdos vivos o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "¿Cuántos cerdos llegan *VIVOS*?\n"
            "_(Ingrese solo el número)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.cerdos_vivos)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        cantidad_vivos = data.get("cerdos_vivos_temp")
        
        # Obtener el total de animales para calcular los muertos
        total_animales = data.get('num_animales', 0)
        cerdos_muertos = total_animales - cantidad_vivos
        
        await state.update_data(
            cerdos_vivos=cantidad_vivos,
            cerdos_muertos=cerdos_muertos
        )
        
        if cerdos_muertos > 0:
            # ALERTA ESPECIAL si hay cerdos muertos
            await message.answer(
                f"✅ Cerdos vivos: *{cantidad_vivos}*\n"
                f"📊 Total de cerdos: *{total_animales}*\n\n"
                f"🚨 *ALERTA: {cerdos_muertos} CERDOS MUERTOS* 🚨\n\n"
                f"⚠️ ¡ATENCIÓN! SE DETECTARON ANIMALES MUERTOS\n"
                f"Cantidad: *{cerdos_muertos}*",
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                f"✅ Cerdos vivos: *{cantidad_vivos}*\n"
                f"📊 Total de cerdos: *{total_animales}*\n"
                f"✅ Sin cerdos muertos",
                parse_mode="Markdown"
            )
        
        # Continuar con el peso de los cerdos vivos
        await message.answer(
            f"¿Cuánto pesan los *{cantidad_vivos} cerdos VIVOS*? _(en kilogramos)_\n"
            f"_(Puede usar decimales con coma)_",
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.peso)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 7. PESO
@dp.message(ConductoresState.peso)
async def procesar_peso(message: types.Message, state: FSMContext):
    """Procesa el peso del pesaje"""
    peso_texto = message.text.strip().replace(",", ".")
    
    try:
        peso = float(peso_texto)
        if peso <= 0:
            await message.answer("⚠️ El peso debe ser mayor a 0.\n\nIntente nuevamente:")
            return
        if peso > 100000:
            await message.answer("⚠️ El peso no puede superar 100,000 kg.\n\nIntente nuevamente:")
            return
    except ValueError:
        await message.answer("⚠️ Peso inválido. Ingrese un número válido (puede usar decimales).\n\nIntente nuevamente:")
        return
    
    await state.update_data(peso_temp=peso)
    await preguntar_confirmacion(message, f"{peso:,.2f} kg", "peso")
    await state.set_state(ConductoresState.confirmar_peso_input)

@dp.message(ConductoresState.confirmar_peso_input)
async def confirmar_peso_input(message: types.Message, state: FSMContext):
    """Confirma el peso o permite modificarlo"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "¿Cuánto pesa? _(en kilogramos)_\n"
            "_(Puede usar decimales con coma)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.peso)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        peso = data.get("peso_temp")
        await state.update_data(peso=peso)
        
        await message.answer(
            f"✅ Peso: *{peso:,.2f} kg*\n\n"
            f"📸 Ahora envíe una *foto del pesaje*:",
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.foto_pesaje)
    else:
        await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

# 9. FOTO DEL PESAJE
@dp.message(ConductoresState.foto_pesaje, F.photo)
async def procesar_foto_pesaje(message: types.Message, state: FSMContext):
    """Procesa la foto del pesaje"""
    # Obtener la foto de mayor resolución
    photo = message.photo[-1]
    file_id = photo.file_id
    
    # Descargar foto
    file = await bot.get_file(file_id)
    os.makedirs("imagenes_pesajes", exist_ok=True)
    
    data = await state.get_data()
    cedula = data.get("cedula")
    placa = data.get("placa")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"pesaje_{placa}_{cedula}_{timestamp}.jpg"
    file_path = os.path.join("imagenes_pesajes", filename)
    
    await bot.download_file(file.file_path, file_path)
    
    # Subir a Drive
    drive_link = upload_to_drive(file_path, filename)
    await state.update_data(foto_pesaje=drive_link or file_path)
    
    # Crear resumen para confirmación
    resumen = crear_resumen_conductor(data)
    
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text="✅ Sí, confirmar")
    keyboard.button(text="❌ No, cancelar")
    keyboard.adjust(1)
    
    await message.answer(
        f"📋 *RESUMEN DEL REGISTRO*\n\n"
        f"{resumen}\n\n"
        f"¿Está seguro de este peso y la información?",
        reply_markup=keyboard.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )
    await state.set_state(ConductoresState.confirmar_peso)

@dp.message(ConductoresState.foto_pesaje)
async def foto_pesaje_no_valida(message: types.Message, state: FSMContext):
    """Handler para cuando no envían una foto"""
    await message.answer("⚠️ Por favor envíe una FOTO del pesaje (no texto).")

# 10. CONFIRMACIÓN FINAL
@dp.message(ConductoresState.confirmar_peso)
async def confirmar_registro_conductor(message: types.Message, state: FSMContext):
    """Confirma y guarda el registro del conductor"""
    texto = message.text.strip().lower()
    
    if "no" in texto or "cancelar" in texto or "❌" in texto:
        await message.answer(
            "❌ Registro cancelado.\n\n"
            "Volviendo al menú principal...",
            reply_markup=ReplyKeyboardRemove()
        )
        await volver_menu_principal(message, state)
        return
    
    if "si" in texto or "confirmar" in texto or "✅" in texto or "sí" in texto:
        # Guardar en base de datos
        data = await state.get_data()
        await guardar_registro_conductor(message, state, data)
    else:
        await message.answer("⚠️ Por favor seleccione una opción válida (Sí o No).")

# ==================== FUNCIONES AUXILIARES CONDUCTORES ==================== #

def crear_resumen_conductor(data: dict) -> str:
    """Crea un resumen legible del registro del conductor"""
    lineas = []
    lineas.append(f"👤 Cédula: {data.get('cedula')}")
    lineas.append(f"🚛 Placa: {data.get('placa')}")
    lineas.append(f"📦 Carga: {data.get('tipo_carga')}")
    
    tipo_carga = data.get('tipo_carga')
    
    if tipo_carga in ["Lechones", "Cerdos Gordos"]:
        lineas.append(f"🐷 Cantidad: {data.get('num_animales')} animales")
        
    elif tipo_carga == "Combustible":
        lineas.append(f"⛽ Tipo: {data.get('tipo_combustible')}")
        lineas.append(f"📊 Galones: {data.get('cantidad_galones'):,.2f}")
        
    elif tipo_carga == "Concentrado":
        lineas.append(f"📋 Número de factura: {data.get('numero_factura')}")
        lineas.append(f"📋 Tipo de alimento: {data.get('tipo_alimento')}")
        lineas.append(f"📋 Kilos comprados: {data.get('kilos_comprados'):,.2f} kg")
    
    lineas.append(f"🏢 Báscula: {data.get('bascula')}")
    
    # Info especial de Bogotá
    if data.get('bascula') == "Bogotá":
        lineas.append(f"✅ Cerdos vivos: {data.get('cerdos_vivos', 0)}")
        if data.get('cerdos_muertos', 0) > 0:
            lineas.append(f"🚨 Cerdos muertos: {data.get('cerdos_muertos')}")
    
    lineas.append(f"⚖️ Peso: {data.get('peso'):,.2f} kg")
    
    return "\n".join(lineas)

async def guardar_registro_conductor(message: types.Message, state: FSMContext, data: dict):
    """Guarda el registro del conductor en la base de datos y envía notificación"""
    
    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            # Crear tabla si no existe
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS conductores (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
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
            ''')
            
            # Insertar registro
            await conn.execute('''
                INSERT INTO conductores (
                    telegram_id, cedula, placa, tipo_carga, num_animales, tipo_combustible,
                    cantidad_galones, factura_dato1, factura_dato2, factura_dato3,
                    factura_foto, bascula, cerdos_vivos, cerdos_muertos, peso, foto_pesaje
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
            ''', 
                data.get('telegram_id'),
                data.get('cedula'),
                data.get('placa'),
                data.get('tipo_carga'),
                data.get('num_animales'),
                data.get('tipo_combustible'),
                data.get('cantidad_galones'),
                data.get('numero_factura'),
                data.get('tipo_alimento'),
                data.get('kilos_comprados'),
                data.get('factura_foto'),
                data.get('bascula'),
                data.get('cerdos_vivos'),
                data.get('cerdos_muertos'),
                data.get('peso'),
                data.get('foto_pesaje')
            )
            
            print("✅ Registro de conductor guardado en base de datos")
    except Exception as e:
        print(f"⚠️ Error guardando en base de datos: {e}")
    finally:
        if conn:
            await release_db_connection(conn)
    
    # Enviar notificación al grupo
    await enviar_notificacion_grupo_conductor(data)
    
    # Confirmar al usuario
    await message.answer(
        "✅ *REGISTRO COMPLETADO EXITOSAMENTE*\n\n"
        "Su pesaje ha sido registrado correctamente.\n\n"
        "Volviendo al menú principal...",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    
    await volver_menu_principal(message, state)

async def enviar_notificacion_grupo_conductor(data: dict):
    """Envía notificación al grupo de Telegram con la información del conductor"""
    if not GROUP_CHAT_ID:
        print("⚠️ GROUP_CHAT_ID no configurado. No se enviará notificación.")
        return
    
    try:
        # Crear mensaje
        mensaje_lineas = ["🚛 *NUEVO REGISTRO DE CONDUCTOR*\n"]
        
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")
        mensaje_lineas.append(f"📅 Fecha: {timestamp}\n")
        
        mensaje_lineas.append(f"👤 Cédula: *{data.get('cedula')}*")
        mensaje_lineas.append(f"🚛 Placa: *{data.get('placa')}*")
        mensaje_lineas.append(f"📦 Tipo de carga: *{data.get('tipo_carga')}*\n")
        
        tipo_carga = data.get('tipo_carga')
        
        # Detalles según tipo de carga
        if tipo_carga in ["Lechones", "Cerdos Gordos"]:
            mensaje_lineas.append(f"🐷 Cantidad de animales: *{data.get('num_animales')}*")
            
        elif tipo_carga == "Combustible":
            mensaje_lineas.append(f"⛽ Tipo de combustible: *{data.get('tipo_combustible')}*")
            mensaje_lineas.append(f"📊 Cantidad: *{data.get('cantidad_galones'):,.2f} galones*")
            
        elif tipo_carga == "Concentrado":
            mensaje_lineas.append("📋 *DATOS DE FACTURA:*")
            mensaje_lineas.append(f"   • Número de factura: {data.get('numero_factura')}")
            mensaje_lineas.append(f"   • Tipo de alimento: {data.get('tipo_alimento')}")
            mensaje_lineas.append(f"   • Kilos comprados: {data.get('kilos_comprados'):,.2f} kg")
            if data.get('factura_foto'):
                mensaje_lineas.append(f"   • [Ver foto de factura]({data.get('factura_foto')})")
        
        mensaje_lineas.append(f"\n🏢 Báscula: *{data.get('bascula')}*")
        
        # Información especial de Bogotá
        if data.get('bascula') == "Bogotá":
            mensaje_lineas.append(f"✅ Cerdos vivos: *{data.get('cerdos_vivos', 0)}*")
            
            cerdos_muertos = data.get('cerdos_muertos', 0)
            if cerdos_muertos > 0:
                # ALERTA ESPECIAL EN MAYÚSCULAS CON EMOJIS
                mensaje_lineas.append("\n" + "🔴" * 15)
                mensaje_lineas.append(f"🚨 *¡¡¡ALERTA CRÍTICA!!!* 🚨")
                mensaje_lineas.append(f"⚠️ *SE MURIERON {cerdos_muertos} CERDOS* ⚠️")
                mensaje_lineas.append("🔴" * 15 + "\n")
        
        mensaje_lineas.append(f"⚖️ Peso registrado: *{data.get('peso'):,.2f} kg*")
        
        if data.get('foto_pesaje'):
            mensaje_lineas.append(f"\n📸 [Ver foto del pesaje]({data.get('foto_pesaje')})")
        
        mensaje = "\n".join(mensaje_lineas)
        
        # Enviar mensaje
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=mensaje,
            parse_mode="Markdown"
        )
        
        print("✅ Notificación enviada al grupo")
        
    except Exception as e:
        print(f"⚠️ Error enviando notificación al grupo: {e}")

# ==================== OPERARIO SITIO 3 - SUBMENÚ ==================== #
@dp.message(RegistroState.sitio3_menu, F.text == "1")
async def sitio3_registro_animales(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 1: Registro de Animales"""
    # Inicializar datos de sesión
    session_id = str(uuid.uuid4())
    await state.update_data(
        sitio3_session_id=session_id,
        sitio3_corrales=[]  # Lista para acumular corrales
    )
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.sitio3_cedula)

@dp.message(RegistroState.sitio3_menu, F.text == "2")
async def sitio3_medicion_silos(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 2: Medición de Silos (Placeholder)"""
    await message.answer(
        "🚧 *MEDICIÓN DE SILOS*\n\n"
        "Esta funcionalidad estará disponible próximamente.\n\n",
        parse_mode="Markdown"
    )
    await volver_menu_sitio3(message, state)

@dp.message(RegistroState.sitio3_menu, F.text == "3")
async def sitio3_descarga_animales(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 3: Descarga de Animales"""
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.descarga_cedula)

# ==================== OPERARIO SITIO 3 - REGISTRO DE ANIMALES ==================== #

# PASO 1: Cédula
@dp.message(RegistroState.sitio3_cedula)
async def sitio3_get_cedula(message: types.Message, state: FSMContext):
    """Captura y valida la cédula del operario"""
    cedula = message.text.strip()

    if not validar_cedula_sitio3(cedula):
        await message.answer(
            "⚠️ Cédula inválida.\n\n"
            "Debe contener solo números y tener entre 6 y 12 dígitos.\n\n"
            "Por favor, intente nuevamente:"
        )
        return

    await state.update_data(sitio3_cedula=cedula)
    await message.answer(
        f"📋 Cédula ingresada: *{cedula}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_confirmar_cedula)

@dp.message(RegistroState.sitio3_confirmar_cedula, F.text == "1")
async def sitio3_confirmar_cedula_si(message: types.Message, state: FSMContext):
    """Confirma cédula y pasa a cantidad de animales"""
    await message.answer("¿Cuántos animales hay en este corral?")
    await state.set_state(RegistroState.sitio3_cantidad_animales)

@dp.message(RegistroState.sitio3_confirmar_cedula, F.text == "2")
async def sitio3_confirmar_cedula_no(message: types.Message, state: FSMContext):
    """Rechaza cédula y vuelve a preguntar"""
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.sitio3_cedula)

@dp.message(RegistroState.sitio3_confirmar_cedula)
async def sitio3_confirmar_cedula_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas en confirmación de cédula"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 2: Cantidad de Animales
@dp.message(RegistroState.sitio3_cantidad_animales)
async def sitio3_get_cantidad(message: types.Message, state: FSMContext):
    """Captura y valida cantidad de animales"""
    cantidad_texto = message.text.strip()

    es_valido, cantidad, mensaje_error = validar_cantidad_animales(cantidad_texto)

    if not es_valido:
        await message.answer(f"⚠️ {mensaje_error}\n\nPor favor, intente nuevamente:")
        return

    # Guardar cantidad temporalmente
    await state.update_data(sitio3_cantidad_temp=cantidad)

    # Si es > 1000, mostrar advertencia especial
    if cantidad > 1000:
        await message.answer(
            "⚠️ *ADVERTENCIA*\n\n"
            f"Está registrando *MÁS de 1000 animales* en un solo corral.\n"
            f"Normalmente el rango es de 0 a 700 animales.\n\n"
            f"Cantidad ingresada: *{cantidad} animales*\n\n"
            "¿Está seguro de continuar?\n\n"
            "1️⃣ Sí, es correcto\n"
            "2️⃣ No, corregir cantidad\n\n"
            "Escriba el número de la opción:",
            parse_mode="Markdown"
        )
    else:
        # Confirmación normal
        await message.answer(
            f"🐷 Cantidad: *{cantidad} animales*\n\n"
            "¿Es correcto?\n\n"
            "1️⃣ Sí, confirmar\n"
            "2️⃣ No, editar\n\n"
            "Escriba el número de la opción:",
            parse_mode="Markdown"
        )

    await state.set_state(RegistroState.sitio3_confirmar_cantidad)

@dp.message(RegistroState.sitio3_confirmar_cantidad, F.text == "1")
async def sitio3_confirmar_cantidad_si(message: types.Message, state: FSMContext):
    """Confirma cantidad y pasa a rango de corrales"""
    await message.answer(
        "¿En qué corrales están los animales?\n\n"
        "Por favor ingrese el rango en formato: *#-#*\n\n"
        "*Ejemplos válidos:*\n"
        "• `0-10`\n"
        "• `15-25`\n"
        "• `1-8`",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_rango_corrales)

@dp.message(RegistroState.sitio3_confirmar_cantidad, F.text == "2")
async def sitio3_confirmar_cantidad_no(message: types.Message, state: FSMContext):
    """Rechaza cantidad y vuelve a preguntar"""
    data = await state.get_data()
    corrales_registrados = data.get('sitio3_corrales', [])

    if len(corrales_registrados) > 0:
        # Si ya hay corrales registrados, especificar que es para un nuevo corral
        await message.answer(
            "¿Cuántos animales hay en ESTE NUEVO CORRAL?\n\n"
            "⚠️ Nota: Ingrese solo la cantidad para este corral,\n"
            "NO la cantidad total acumulada."
        )
    else:
        await message.answer("¿Cuántos animales hay en este corral?")

    await state.set_state(RegistroState.sitio3_cantidad_animales)

@dp.message(RegistroState.sitio3_confirmar_cantidad)
async def sitio3_confirmar_cantidad_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 3: Rango de Corrales
@dp.message(RegistroState.sitio3_rango_corrales)
async def sitio3_get_rango(message: types.Message, state: FSMContext):
    """Captura y valida rango de corrales"""
    rango = message.text.strip()

    es_valido, mensaje_error = validar_rango_corrales(rango)

    if not es_valido:
        await message.answer(
            f"⚠️ {mensaje_error}\n\n"
            "Por favor ingrese el rango en formato: *#-#*\n"
            "Ejemplo: `0-10`",
            parse_mode="Markdown"
        )
        return

    await state.update_data(sitio3_rango_temp=rango)
    await message.answer(
        f"📍 Corrales: *{rango}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_confirmar_rango)

@dp.message(RegistroState.sitio3_confirmar_rango, F.text == "1")
async def sitio3_confirmar_rango_si(message: types.Message, state: FSMContext):
    """Confirma rango y pasa a tipo de comida"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="Levante")
    builder.button(text="Engorde Medicado")
    builder.button(text="Finalizador")
    builder.adjust(2)  # 2 botones por fila

    await message.answer(
        "¿Qué tipo de comida están consumiendo estos animales?",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.sitio3_tipo_comida)

@dp.message(RegistroState.sitio3_confirmar_rango, F.text == "2")
async def sitio3_confirmar_rango_no(message: types.Message, state: FSMContext):
    """Rechaza rango y vuelve a preguntar"""
    await message.answer(
        "¿En qué corrales están los animales?\n\n"
        "Por favor ingrese el rango en formato: *#-#*\n\n"
        "*Ejemplos válidos:*\n"
        "• `0-10`\n"
        "• `15-25`\n"
        "• `1-8`",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_rango_corrales)

@dp.message(RegistroState.sitio3_confirmar_rango)
async def sitio3_confirmar_rango_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 4: Tipo de Comida
@dp.message(RegistroState.sitio3_tipo_comida, F.text.in_(["Levante", "Engorde", "Finalizador"]))
async def sitio3_get_tipo_comida(message: types.Message, state: FSMContext):
    """Captura tipo de comida seleccionado"""
    tipo_comida = message.text

    await state.update_data(sitio3_tipo_comida_temp=tipo_comida)
    await message.answer(
        f"🍽️ Tipo de comida: *{tipo_comida}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.sitio3_confirmar_tipo_comida)

@dp.message(RegistroState.sitio3_tipo_comida)
async def sitio3_tipo_comida_invalido(message: types.Message, state: FSMContext):
    """Handler para opciones inválidas"""
    await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

@dp.message(RegistroState.sitio3_confirmar_tipo_comida, F.text == "1")
async def sitio3_confirmar_tipo_comida_si(message: types.Message, state: FSMContext):
    """Confirma tipo de comida y guarda el corral"""
    data = await state.get_data()

    # Agregar este corral a la lista de corrales
    corrales = data.get('sitio3_corrales', [])
    corrales.append({
        'cantidad': data['sitio3_cantidad_temp'],
        'rango': data['sitio3_rango_temp'],
        'tipo_comida': data['sitio3_tipo_comida_temp']
    })

    await state.update_data(sitio3_corrales=corrales)

    # Mostrar resumen y preguntar si desea agregar más
    total_animales = sum(c['cantidad'] for c in corrales)

    resumen = "✅ Corral registrado correctamente.\n\n"
    resumen += "📊 *Resumen hasta ahora:*\n"
    for i, corral in enumerate(corrales, 1):
        resumen += f"\n🔹 Corrales {corral['rango']}\n"
        resumen += f"   • Animales: {corral['cantidad']}\n"
        resumen += f"   • Comida: {corral['tipo_comida']}\n"

    resumen += f"\n━━━━━━━━━━━━━━━━━━━━\n"
    resumen += f"🏋️ *TOTAL: {total_animales:,} animales*\n"

    await message.answer(resumen, parse_mode="Markdown")

    # Preguntar si desea registrar otro corral
    builder = ReplyKeyboardBuilder()
    builder.button(text="✅ Sí, otro corral")
    builder.button(text="❌ No, terminar")
    builder.adjust(2)

    await message.answer(
        "¿Desea registrar otro corral?",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.sitio3_agregar_mas)

@dp.message(RegistroState.sitio3_confirmar_tipo_comida, F.text == "2")
async def sitio3_confirmar_tipo_comida_no(message: types.Message, state: FSMContext):
    """Rechaza tipo de comida y vuelve a preguntar"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="Levante")
    builder.button(text="Engorde")
    builder.button(text="Finalizador")
    builder.adjust(2)

    await message.answer(
        "¿Qué tipo de comida están consumiendo estos animales?",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.sitio3_tipo_comida)

@dp.message(RegistroState.sitio3_confirmar_tipo_comida)
async def sitio3_confirmar_tipo_comida_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 5: Agregar Más Corrales o Terminar
@dp.message(RegistroState.sitio3_agregar_mas, F.text.in_(["✅ Sí, otro corral", "Sí", "Si", "1"]))
async def sitio3_agregar_otro_corral(message: types.Message, state: FSMContext):
    """Usuario quiere agregar otro corral"""
    await message.answer(
        "¿Cuántos animales hay en ESTE NUEVO CORRAL?\n\n"
        "⚠️ Nota: Ingrese solo la cantidad para este corral,\n"
        "NO la cantidad total acumulada.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.sitio3_cantidad_animales)

@dp.message(RegistroState.sitio3_agregar_mas, F.text.in_(["❌ No, terminar", "No", "2"]))
async def sitio3_terminar_registro(message: types.Message, state: FSMContext):
    """Usuario termina el registro - Guardar en BD y notificar"""
    await message.answer("⏳ Guardando registros...", reply_markup=types.ReplyKeyboardRemove())

    data = await state.get_data()
    cedula = data.get('sitio3_cedula')
    corrales = data.get('sitio3_corrales', [])
    session_id = data.get('sitio3_session_id')

    if not corrales:
        await message.answer("⚠️ No hay corrales registrados para guardar.")
        await volver_menu_sitio3(message, state)
        return

    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            fecha_registro = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Insertar cada corral como una fila separada
            telegram_user_id = message.from_user.id
            for corral in corrales:
                await conn.execute('''
                    INSERT INTO operario_sitio3_animales
                    (cedula_operario, cantidad_animales, rango_corrales, tipo_comida, fecha_registro, session_id, telegram_user_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''', cedula, corral['cantidad'], corral['rango'], corral['tipo_comida'], fecha_registro, session_id, telegram_user_id)

            print(f"✅ {len(corrales)} corrales guardados en BD (session: {session_id})")
        else:
            print("⚠️ No se pudo obtener conexión a la base de datos")

    except Exception as e:
        print(f"❌ Error guardando en base de datos: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

    # Calcular totales
    total_animales = sum(c['cantidad'] for c in corrales)
    total_corrales = len(corrales)

    # Generar notificación para el grupo de Telegram
    if GROUP_CHAT_ID:
        try:
            fecha_formateada = datetime.now().strftime('%d/%m/%Y %H:%M')

            mensaje_grupo = (
                "🐷 *NUEVO REGISTRO DE ANIMALES - SITIO 3*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Operario: `{cedula}`\n"
                f"🕒 Fecha: {fecha_formateada}\n\n"
                "📊 *CORRALES REGISTRADOS:*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )

            for corral in corrales:
                mensaje_grupo += (
                    f"🔹 *Corrales {corral['rango']}*\n"
                    f"   • Animales: {corral['cantidad']}\n"
                    f"   • Comida: {corral['tipo_comida']}\n\n"
                )

            mensaje_grupo += (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏋️ *TOTAL: {total_animales:,} animales registrados*"
            )

            await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")
            print("✅ Notificación enviada al grupo")

        except Exception as e:
            print(f"⚠️ Error al enviar notificación al grupo: {e}")

    # Mostrar resumen al usuario
    resumen_usuario = (
        "✅ *Registro completado exitosamente*\n\n"
        "📊 *Resumen:*\n\n"
        f"• Total de corrales: {total_corrales}\n"
        f"• Total de animales: *{total_animales:,}*\n\n"
        "Gracias por registrar la información."
    )

    await message.answer(resumen_usuario, parse_mode="Markdown")

    # Volver al menú principal
    await asyncio.sleep(1)
    await volver_menu_principal(message, state)

@dp.message(RegistroState.sitio3_agregar_mas)
async def sitio3_agregar_mas_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas"""
    await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

# ==================== OPERARIO SITIO 3 - DESCARGA DE ANIMALES ==================== #

# PASO 1: Cédula
@dp.message(RegistroState.descarga_cedula)
async def descarga_get_cedula(message: types.Message, state: FSMContext):
    """Captura y valida la cédula del operario"""
    cedula = message.text.strip()

    if not validar_cedula_sitio3(cedula):
        await message.answer(
            "⚠️ Cédula inválida.\n\n"
            "Debe contener solo números y tener entre 6 y 12 dígitos.\n\n"
            "Por favor, intente nuevamente:"
        )
        return

    await state.update_data(descarga_cedula=cedula)
    await message.answer(
        f"📋 Cédula ingresada: *{cedula}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.descarga_confirmar_cedula)

@dp.message(RegistroState.descarga_confirmar_cedula, F.text == "1")
async def descarga_confirmar_cedula_si(message: types.Message, state: FSMContext):
    """Confirma cédula y pasa a cantidad de lechones"""
    await message.answer(
        "🐷 Ingrese la cantidad de lechones\n\n"
        "⚠️ Nota: Los lechones son cerdos jóvenes que\n"
        "están llegando a la granja.\n\n"
        "Cantidad:"
    )
    await state.set_state(RegistroState.descarga_cantidad_lechones)

@dp.message(RegistroState.descarga_confirmar_cedula, F.text == "2")
async def descarga_confirmar_cedula_no(message: types.Message, state: FSMContext):
    """Rechaza cédula y vuelve a preguntar"""
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.descarga_cedula)

@dp.message(RegistroState.descarga_confirmar_cedula)
async def descarga_confirmar_cedula_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas en confirmación de cédula"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 2: Cantidad de Lechones
@dp.message(RegistroState.descarga_cantidad_lechones)
async def descarga_get_cantidad(message: types.Message, state: FSMContext):
    """Captura y valida cantidad de lechones"""
    cantidad_texto = message.text.strip()

    es_valido, cantidad, mensaje_error = validar_cantidad_lechones(cantidad_texto)

    if not es_valido:
        await message.answer(f"⚠️ {mensaje_error}\n\nPor favor, intente nuevamente:")
        return

    # Guardar cantidad temporalmente
    await state.update_data(descarga_cantidad=cantidad)

    # Si es > 1000, mostrar advertencia especial
    if cantidad > 1000:
        await message.answer(
            "⚠️ *ADVERTENCIA - CANTIDAD ALTA*\n\n"
            f"Está registrando más de 1000 lechones en una sola descarga.\n\n"
            f"Cantidad ingresada: *{cantidad} lechones*\n\n"
            "¿Está seguro de que es correcta?\n\n"
            "1️⃣ Sí, es correcto\n"
            "2️⃣ No, corregir cantidad\n\n"
            "Escriba el número de la opción:",
            parse_mode="Markdown"
        )
    else:
        # Confirmación normal
        await message.answer(
            f"🐷 Lechones a descargar: *{cantidad}*\n\n"
            "¿Es correcto?\n\n"
            "1️⃣ Sí, confirmar\n"
            "2️⃣ No, editar\n\n"
            "Escriba el número de la opción:",
            parse_mode="Markdown"
        )

    await state.set_state(RegistroState.descarga_confirmar_cantidad)

@dp.message(RegistroState.descarga_confirmar_cantidad, F.text == "1")
async def descarga_confirmar_cantidad_si(message: types.Message, state: FSMContext):
    """Confirma cantidad y pasa a rango de corrales"""
    await message.answer(
        "📍 Ingrese el rango de corrales\n\n"
        "Formato requerido: *#-#*\n\n"
        "*Ejemplos válidos:*\n"
        "• `1-5` (corrales del 1 al 5)\n"
        "• `10-15` (corrales del 10 al 15)\n"
        "• `20-25` (corrales del 20 al 25)\n\n"
        "Por favor ingrese el rango:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.descarga_rango_corrales)

@dp.message(RegistroState.descarga_confirmar_cantidad, F.text == "2")
async def descarga_confirmar_cantidad_no(message: types.Message, state: FSMContext):
    """Rechaza cantidad y vuelve a preguntar"""
    await message.answer(
        "🐷 Ingrese la cantidad de lechones\n\n"
        "⚠️ Nota: Los lechones son cerdos jóvenes que\n"
        "están llegando a la granja.\n\n"
        "Cantidad:"
    )
    await state.set_state(RegistroState.descarga_cantidad_lechones)

@dp.message(RegistroState.descarga_confirmar_cantidad)
async def descarga_confirmar_cantidad_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 3: Rango de Corrales
@dp.message(RegistroState.descarga_rango_corrales)
async def descarga_get_rango(message: types.Message, state: FSMContext):
    """Captura y valida rango de corrales"""
    rango = message.text.strip()

    es_valido, mensaje_error = validar_rango_corrales(rango)

    if not es_valido:
        await message.answer(
            f"⚠️ {mensaje_error}\n\n"
            "Por favor ingrese el rango en formato: *#-#*\n\n"
            "*Ejemplos:*\n"
            "• `1-5`\n"
            "• `10-15`\n"
            "• `20-25`",
            parse_mode="Markdown"
        )
        return

    await state.update_data(descarga_rango=rango)
    await message.answer(
        f"📍 Corrales de descarga: *{rango}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.descarga_confirmar_rango)

@dp.message(RegistroState.descarga_confirmar_rango, F.text == "1")
async def descarga_confirmar_rango_si(message: types.Message, state: FSMContext):
    """Confirma rango y pasa a número de lote"""
    await message.answer(
        "🏷️ Ingrese el número de LOTE\n\n"
        "⚠️ Nota: El lote es el identificador único\n"
        "de este grupo de animales para trazabilidad.\n\n"
        "Formato típico: YYYY-NNN\n"
        "Ejemplos: 2024-001, 2024-045, 2025-123\n\n"
        "Número de lote:"
    )
    await state.set_state(RegistroState.descarga_numero_lote)

@dp.message(RegistroState.descarga_confirmar_rango, F.text == "2")
async def descarga_confirmar_rango_no(message: types.Message, state: FSMContext):
    """Rechaza rango y vuelve a preguntar"""
    await message.answer(
        "📍 Ingrese el rango de corrales\n\n"
        "Formato requerido: *#-#*\n\n"
        "*Ejemplos válidos:*\n"
        "• `1-5` (corrales del 1 al 5)\n"
        "• `10-15` (corrales del 10 al 15)\n"
        "• `20-25` (corrales del 20 al 25)\n\n"
        "Por favor ingrese el rango:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.descarga_rango_corrales)

@dp.message(RegistroState.descarga_confirmar_rango)
async def descarga_confirmar_rango_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 4: Número de Lote
@dp.message(RegistroState.descarga_numero_lote)
async def descarga_get_lote(message: types.Message, state: FSMContext):
    """Captura y valida número de lote"""
    numero_lote = message.text.strip()

    es_valido, mensaje_error = validar_numero_lote(numero_lote)

    if not es_valido:
        await message.answer(
            f"⚠️ {mensaje_error}\n\n"
            "*Formato válido:*\n"
            "• Solo letras, números, guiones (-) y guiones bajos (_)\n"
            "• Entre 3 y 30 caracteres\n"
            "• Sin espacios\n\n"
            "*Ejemplos válidos:*\n"
            "• `2024-001`\n"
            "• `2025-123`\n"
            "• `LOTE_456`\n\n"
            "Por favor, intente nuevamente:",
            parse_mode="Markdown"
        )
        return

    await state.update_data(descarga_lote=numero_lote)
    await message.answer(
        f"🏷️ Lote: *{numero_lote}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.descarga_confirmar_lote)

@dp.message(RegistroState.descarga_confirmar_lote, F.text == "1")
async def descarga_confirmar_lote_si(message: types.Message, state: FSMContext):
    """Confirma lote y procede a guardar"""
    await message.answer("⏳ Guardando registro de descarga...")

    data = await state.get_data()
    cedula = data.get('descarga_cedula')
    cantidad = data.get('descarga_cantidad')
    rango_corrales = data.get('descarga_rango')
    numero_lote = data.get('descarga_lote')

    # Generar identificador LOTE+CORRAL
    identificador = f"{numero_lote}+{rango_corrales}"

    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            fecha_registro = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            telegram_user_id = message.from_user.id

            # Insertar registro
            await conn.execute('''
                INSERT INTO operario_sitio3_descarga_animales
                (cedula_operario, cantidad_lechones, rango_corrales, numero_lote, identificador, fecha_registro, telegram_user_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            ''', cedula, cantidad, rango_corrales, numero_lote, identificador, fecha_registro, telegram_user_id)

            print(f"✅ Descarga guardada en BD: {identificador}")
        else:
            print("⚠️ No se pudo obtener conexión a la base de datos")

    except Exception as e:
        print(f"❌ Error guardando en base de datos: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

    # Generar notificación para el grupo de Telegram
    if GROUP_CHAT_ID:
        try:
            fecha_formateada = datetime.now().strftime('%d/%m/%Y %H:%M')

            mensaje_grupo = (
                "🚚 *NUEVA DESCARGA DE LECHONES - SITIO 3*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Operario: `{cedula}`\n"
                f"🕒 Fecha: {fecha_formateada}\n\n"
                "📦 *INFORMACIÓN DE DESCARGA:*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🏷️ Identificador: *{identificador}*\n"
                f"📍 Corrales: {rango_corrales}\n"
                f"🐷 Cantidad: {cantidad} lechones\n"
                f"🏷️ Lote: {numero_lote}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ Descarga registrada exitosamente"
            )

            await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")
            print("✅ Notificación enviada al grupo")

        except Exception as e:
            print(f"⚠️ Error al enviar notificación al grupo: {e}")

    # Mostrar resumen al usuario
    resumen_usuario = (
        "✅ *Descarga registrada exitosamente*\n\n"
        "📊 *Resumen:*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ Lote: {numero_lote}\n"
        f"📍 Corrales: {rango_corrales}\n"
        f"🐷 Lechones: {cantidad}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Los datos se han guardado correctamente."
    )

    await message.answer(resumen_usuario, parse_mode="Markdown")

    # Volver al menú principal
    await asyncio.sleep(1)
    await volver_menu_principal(message, state)

@dp.message(RegistroState.descarga_confirmar_lote, F.text == "2")
async def descarga_confirmar_lote_no(message: types.Message, state: FSMContext):
    """Rechaza lote y vuelve a preguntar"""
    await message.answer(
        "🏷️ Ingrese el número de LOTE\n\n"
        "⚠️ Nota: El lote es el identificador único\n"
        "de este grupo de animales para trazabilidad.\n\n"
        "Formato típico: YYYY-NNN\n"
        "Ejemplos: 2024-001, 2024-045, 2025-123\n\n"
        "Número de lote:"
    )
    await state.set_state(RegistroState.descarga_numero_lote)

@dp.message(RegistroState.descarga_confirmar_lote)
async def descarga_confirmar_lote_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# ==================== FIN DESCARGA DE ANIMALES ==================== #

# ==================== FIN OPERARIO SITIO 3 ==================== #

@dp.message(RegistroState.consulta_silo)
async def mostrar_capacidad_silo(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Por favor ingrese un número de silo válido.")
        return
    
    silo_numero = int(message.text)
    
    # Conectar a la base de datos para consultar
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            # Consultar todos los registros de este silo desde la tabla normalizada
            registros = await conn.fetch('''
                SELECT s.peso, s.fecha, r.camion_id
                FROM silos s
                JOIN registros r ON s.registro_id = r.id
                WHERE s.numero_silo = $1
                ORDER BY s.fecha DESC
                LIMIT 20
            ''', silo_numero)
            
            if registros:
                # Calcular capacidad total del silo
                total_silo = sum(float(reg['peso']) for reg in registros)
                detalle = f"📊 *Capacidad del Silo {silo_numero}*\n\n"
                detalle += f"📦 *Total acumulado:* {total_silo:.1f} kg\n"
                detalle += f"📋 *Últimos {len(registros)} registros:*\n\n"
                
                for reg in registros:
                    peso_silo = float(reg['peso'])
                    # Formatear fecha
                    fecha_obj = reg['fecha']
                    if hasattr(fecha_obj, 'strftime'):
                        fecha_formato = fecha_obj.strftime('%d/%m/%Y %H:%M')
                    else:
                        fecha_formato = str(fecha_obj)[:16]
                    
                    detalle += f"🚚 {reg['camion_id']}: {peso_silo} kg - {fecha_formato}\n"
                
                await message.answer(detalle, parse_mode="Markdown")
            else:
                await message.answer(f"⚠️ No se encontraron registros para el Silo {silo_numero}")
    
    except Exception as e:
        print(f"⚠️ Error consultando capacidad de silo: {e}")
        import traceback
        traceback.print_exc()
        await message.answer("⚠️ Error al consultar la base de datos")
    finally:
        if conn:
            await release_db_connection(conn)
    
    # Volver al menú principal
    await volver_menu_principal(message, state)

# ==================== RESTAR PESO DE SILO ==================== #
@dp.message(RegistroState.restar_silo_numero)
async def get_numero_silo_restar(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Por favor ingrese un número de silo válido.")
        return
    
    await state.update_data(silo_a_restar=int(message.text))
    await message.answer(f"¿Cuánto peso desea restar del Silo {message.text}? (en kg):")
    await state.set_state(RegistroState.restar_silo_peso)

@dp.message(RegistroState.restar_silo_peso)
async def pedir_confirmacion_restar(message: types.Message, state: FSMContext):
    if not validar_peso(message.text):
        await message.answer("⚠️ Ingrese un peso válido (use coma para decimales).")
        return
    
    data = await state.get_data()
    silo_numero = data.get('silo_a_restar')
    await state.update_data(peso_a_restar_temporal=message.text)
    
    await message.answer(
        f"⚖️ Restar *{message.text} kg* del Silo {silo_numero}\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_restar_peso)

@dp.message(RegistroState.confirmar_restar_peso, F.text == "1")
async def restar_peso_del_silo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    silo_numero = data.get('silo_a_restar')
    peso_str = data.get('peso_a_restar_temporal')
    peso_a_restar = float(peso_str.replace(",", "."))
    
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            # Insertar un registro negativo para restar del total
            await conn.execute('''
                INSERT INTO silos (registro_id, numero_silo, peso, fecha)
                VALUES (NULL, $1, $2, NOW())
            ''', silo_numero, -peso_a_restar)
            
            # Consultar el nuevo total
            total_actual = await conn.fetchval('''
                SELECT COALESCE(SUM(peso), 0) FROM silos WHERE numero_silo = $1
            ''', silo_numero)
            
            await message.answer(
                f"✅ Se restaron {peso_a_restar} kg del Silo {silo_numero}\n\n"
                f"📦 Capacidad actual del Silo {silo_numero}: {total_actual:.1f} kg"
            )
            
    except Exception as e:
        print(f"⚠️ Error restando peso: {e}")
        import traceback
        traceback.print_exc()
        await message.answer("⚠️ Error al actualizar la base de datos")
    finally:
        if conn:
            await release_db_connection(conn)
    
    # Volver al menú principal
    await volver_menu_principal(message, state)

@dp.message(RegistroState.confirmar_restar_peso, F.text == "2")
async def editar_restar_peso(message: types.Message, state: FSMContext):
    data = await state.get_data()
    silo_numero = data.get('silo_a_restar')
    await message.answer(f"¿Cuánto peso desea restar del Silo {silo_numero}? (en kg):")
    await state.set_state(RegistroState.restar_silo_peso)

@dp.message(RegistroState.cedula)
async def get_cedula(message: types.Message, state: FSMContext):
    if not validar_cedula(message.text):
        await message.answer("⚠️ Ingrese solo números (sin letras ni símbolos).")
        return
    await state.update_data(cedula=message.text)
    
    await message.answer(
        f"📋 Cédula ingresada: *{message.text}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_cedula)

@dp.message(RegistroState.confirmar_cedula, F.text == "1")
async def confirmar_cedula(message: types.Message, state: FSMContext):
    await message.answer(
        "¿Es usted transportador o trabajador?\n\n"
        "1️⃣ Transportador\n"
        "2️⃣ Trabajador\n\n"
        "Escriba el número de la opción:"
    )
    await state.set_state(RegistroState.tipo_empleado)

@dp.message(RegistroState.confirmar_cedula, F.text == "2")
async def editar_cedula(message: types.Message, state: FSMContext):
    await message.answer("Ingrese la cédula nuevamente:")
    await state.set_state(RegistroState.cedula)

@dp.message(RegistroState.tipo_empleado, F.text == "1")
async def get_tipo_transportador(message: types.Message, state: FSMContext):
    await state.update_data(tipo_empleado="Transportador")
    await message.answer(
        "👷 Tipo seleccionado: *Transportador*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_tipo_empleado)

@dp.message(RegistroState.tipo_empleado, F.text == "2")
async def get_tipo_trabajador(message: types.Message, state: FSMContext):
    await state.update_data(tipo_empleado="Trabajador")
    await message.answer(
        "👷 Tipo seleccionado: *Trabajador*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_tipo_empleado)

@dp.message(RegistroState.tipo_empleado)
async def tipo_empleado_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor escriba 1 para Transportador o 2 para Trabajador.")

@dp.message(RegistroState.confirmar_tipo_empleado, F.text == "1")
async def confirmar_tipo_empleado(message: types.Message, state: FSMContext):
    await message.answer("Ingrese la placa del camión (3 letras y 3 números):")
    await state.set_state(RegistroState.camion)

@dp.message(RegistroState.confirmar_tipo_empleado, F.text == "2")
async def editar_tipo_empleado(message: types.Message, state: FSMContext):
    await message.answer(
        "¿Es usted transportador o trabajador?\n\n"
        "1️⃣ Transportador\n"
        "2️⃣ Trabajador\n\n"
        "Escriba el número de la opción:"
    )
    await state.set_state(RegistroState.tipo_empleado)

@dp.message(RegistroState.camion)
async def get_camion(message: types.Message, state: FSMContext):
    if not validar_placa(message.text):
        await message.answer("⚠️ Placa inválida. Ejemplo: HHW926.")
        return
    await state.update_data(camion=message.text.upper())
    
    await message.answer(
        f"🚚 Placa ingresada: *{message.text.upper()}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_camion)

@dp.message(RegistroState.confirmar_camion, F.text == "1")
async def confirmar_camion(message: types.Message, state: FSMContext):
    builder = ReplyKeyboardBuilder()
    builder.button(text="Cerdo vivo")
    builder.button(text="Canales frías")
    builder.button(text="Desposte")
    builder.button(text="Pedidos")
    builder.button(text="Gasolina")
    builder.button(text="Otros")
    builder.adjust(2)  # 2 botones por fila
    await message.answer("¿Qué tipo de carga transporta?", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(RegistroState.tipo_carga)

@dp.message(RegistroState.confirmar_camion, F.text == "2")
async def editar_camion(message: types.Message, state: FSMContext):
    await message.answer("Ingrese la placa del camión nuevamente:")
    await state.set_state(RegistroState.camion)

# ==================== TIPO DE CARGA ==================== #
@dp.message(RegistroState.tipo_carga, F.text.in_(["Cerdo vivo", "Canales frías", "Desposte", "Pedidos", "Gasolina"]))
async def get_tipo_carga(message: types.Message, state: FSMContext):
    await state.update_data(tipo_carga=message.text)
    await message.answer(
        f"📦 Tipo de carga: *{message.text}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.confirmar_tipo_carga)

@dp.message(RegistroState.tipo_carga, F.text == "Otros")
async def get_tipo_carga_otros(message: types.Message, state: FSMContext):
    await message.answer(
        "Por favor especifique el tipo de carga:",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.especificar_otros)

@dp.message(RegistroState.especificar_otros)
async def especificar_otros_carga(message: types.Message, state: FSMContext):
    tipo_especifico = message.text.strip()
    if not tipo_especifico:
        await message.answer("⚠️ Por favor ingrese un tipo de carga válido.")
        return

    tipo_carga_completo = f"Otros - {tipo_especifico}"
    await state.update_data(tipo_carga=tipo_carga_completo)
    await message.answer(
        f"📦 Tipo de carga: *{tipo_carga_completo}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_tipo_carga)

@dp.message(RegistroState.tipo_carga)
async def tipo_carga_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

@dp.message(RegistroState.confirmar_tipo_carga, F.text == "1")
async def confirmar_tipo_carga(message: types.Message, state: FSMContext):
    builder = ReplyKeyboardBuilder()
    builder.button(text="Origen")
    builder.button(text="Destino")
    builder.adjust(2)
    await message.answer("Seleccione el tipo de pesaje (Origen o Destino):", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(RegistroState.tipo)

@dp.message(RegistroState.confirmar_tipo_carga, F.text == "2")
async def editar_tipo_carga(message: types.Message, state: FSMContext):
    builder = ReplyKeyboardBuilder()
    builder.button(text="Cerdo vivo")
    builder.button(text="Canales frías")
    builder.button(text="Desposte")
    builder.button(text="Pedidos")
    builder.button(text="Gasolina")
    builder.button(text="Otros")
    builder.adjust(2)
    await message.answer("¿Qué tipo de carga transporta?", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(RegistroState.tipo_carga)

# ==================== ORIGEN ==================== #
@dp.message(RegistroState.tipo, F.text.lower() == "origen")
async def origen_peso(message: types.Message, state: FSMContext):
    await state.update_data(tipo="Origen")
    await message.answer("Ingrese el peso en kg (use coma para decimales):", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(RegistroState.peso_origen)

@dp.message(RegistroState.peso_origen)
async def origen_confirmar_peso(message: types.Message, state: FSMContext):
    if not validar_peso(message.text):
        await message.answer("⚠️ Ingrese solo números (coma para decimales).")
        return
    await state.update_data(peso=message.text)
    
    await message.answer(
        f"⚖️ Peso ingresado: *{message.text} kg*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_peso_origen)

@dp.message(RegistroState.confirmar_peso_origen, F.text == "1")
async def confirmar_peso_origen(message: types.Message, state: FSMContext):
    await message.answer("Envíe la foto del pesaje:")
    await state.set_state(RegistroState.foto)

@dp.message(RegistroState.confirmar_peso_origen, F.text == "2")
async def editar_peso_origen(message: types.Message, state: FSMContext):
    await message.answer("Ingrese el peso nuevamente:")
    await state.set_state(RegistroState.peso_origen)

# ==================== DESTINO ==================== #
@dp.message(RegistroState.tipo, F.text.lower() == "destino")
async def destino_bascula(message: types.Message, state: FSMContext):
    await state.update_data(tipo="Destino", silos=[], total_silos=0)
    await message.answer("Ingrese el peso de la báscula general (en kg, use coma para decimales):", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(RegistroState.peso_bascula_destino)

@dp.message(RegistroState.peso_bascula_destino)
async def destino_confirmar_bascula(message: types.Message, state: FSMContext):
    if not validar_peso(message.text):
        await message.answer("⚠️ Ingrese solo números (coma para decimales).")
        return
    
    peso_bascula = float(message.text.replace(",", "."))
    await state.update_data(peso_bascula_general=peso_bascula)
    
    await message.answer(
        f"⚖️ Peso de báscula: *{message.text} kg*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_peso_bascula)

@dp.message(RegistroState.confirmar_peso_bascula, F.text == "1")
async def confirmar_bascula(message: types.Message, state: FSMContext):
    await message.answer("Ingrese el número del primer silo (1 a 4):")
    await state.set_state(RegistroState.silo_num)

@dp.message(RegistroState.confirmar_peso_bascula, F.text == "2")
async def editar_bascula(message: types.Message, state: FSMContext):
    await message.answer("Ingrese el peso de la báscula nuevamente:")
    await state.set_state(RegistroState.peso_bascula_destino)

@dp.message(RegistroState.silo_num)
async def destino_peso_silo(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or not (1 <= int(message.text) <= 4):
        await message.answer("⚠️ El número de silo debe estar entre 1 y 4.")
        return
    
    await state.update_data(silo_actual=int(message.text))
    await message.answer(f"¿Cuánto peso se descargó en el Silo {message.text}? (en kg, use coma para decimales):")
    await state.set_state(RegistroState.silo_peso)

@dp.message(RegistroState.silo_peso)
async def destino_pedir_confirmacion_silo(message: types.Message, state: FSMContext):
    if not validar_peso(message.text):
        await message.answer("⚠️ Ingrese solo números (coma para decimales).")
        return
    
    data = await state.get_data()
    silo_actual = data.get('silo_actual')
    await state.update_data(peso_silo_temporal=message.text)
    
    await message.answer(
        f"⚖️ Silo {silo_actual}: *{message.text} kg*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.confirmar_silo_peso)

@dp.message(RegistroState.confirmar_silo_peso, F.text == "1")
async def destino_confirmar_silo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    peso_silo_str = data.get('peso_silo_temporal')
    peso_silo = float(peso_silo_str.replace(",", "."))
    silo_actual = data.get('silo_actual')
    
    # Agregar silo a la lista
    silos = data.get('silos', [])
    silos.append({'numero': silo_actual, 'peso': peso_silo})
    
    # Calcular total acumulado
    total_silos = sum(s['peso'] for s in silos)
    peso_bascula = data.get('peso_bascula_general')
    
    await state.update_data(silos=silos, total_silos=total_silos)
    
    # Mostrar resumen actual
    resumen_silos = "\n".join([f"  • Silo {s['numero']}: {s['peso']} kg" for s in silos])
    mensaje = (
        f"✅ Silo {silo_actual} registrado: {peso_silo} kg\n\n"
        f"📊 Resumen actual:\n{resumen_silos}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Total descargado: {total_silos} kg\n"
        f"Peso báscula: {peso_bascula} kg\n"
        f"Restante: {peso_bascula - total_silos} kg\n\n"
    )
    
    # Verificar si ya alcanzó o superó el peso
    if total_silos >= peso_bascula:
        if abs(total_silos - peso_bascula) <= 0.1:  # Tolerancia de 0.1 kg
            await message.answer(mensaje + "✅ Total correcto. Envíe la foto del pesaje:")
            await state.set_state(RegistroState.foto)
        else:
            exceso = total_silos - peso_bascula
            await message.answer(
                mensaje + 
                f"⚠️ EXCESO de {exceso} kg!\n"
                f"Se superó el peso de la báscula. Verifique los valores.\n\n"
                f"Envíe la foto del pesaje:"
            )
            await state.set_state(RegistroState.foto)
    else:
        # Preguntar si quiere agregar otro silo
        builder = ReplyKeyboardBuilder()
        builder.button(text="Sí, agregar otro silo")
        builder.button(text="No, terminar")
        builder.adjust(2)
        await message.answer(mensaje + "¿Desea descargar en otro silo?", reply_markup=builder.as_markup(resize_keyboard=True))
        await state.set_state(RegistroState.silo_num)

@dp.message(RegistroState.confirmar_silo_peso, F.text == "2")
async def editar_peso_silo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    silo_actual = data.get('silo_actual')
    await message.answer(f"Ingrese nuevamente el peso para el Silo {silo_actual}:")
    await state.set_state(RegistroState.silo_peso)

# Handler para cuando el usuario decide agregar otro silo o terminar
@dp.message(RegistroState.silo_num, F.text.lower().in_(["sí, agregar otro silo", "si, agregar otro silo", "sí", "si"]))
async def destino_otro_silo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    silos_usados = [s['numero'] for s in data.get('silos', [])]
    silos_disponibles = [str(i) for i in range(1, 5) if i not in silos_usados]
    
    if not silos_disponibles:
        await message.answer("⚠️ Ya se usaron todos los silos (1-4). Envíe la foto del pesaje:")
        await state.set_state(RegistroState.foto)
        return
    
    await message.answer(f"Ingrese el número del siguiente silo ({', '.join(silos_disponibles)}):")

@dp.message(RegistroState.silo_num, F.text.lower().in_(["no, terminar", "no"]))
async def destino_terminar_silos(message: types.Message, state: FSMContext):
    data = await state.get_data()
    total_silos = data.get('total_silos', 0)
    peso_bascula = data.get('peso_bascula_general', 0)
    diferencia = abs(peso_bascula - total_silos)
    
    if diferencia > 0.1:  # Tolerancia de 0.1 kg
        await message.answer(
            f"⚠️ ADVERTENCIA: Falta descargar {peso_bascula - total_silos} kg\n"
            f"Total silos: {total_silos} kg\n"
            f"Peso báscula: {peso_bascula} kg\n\n"
            f"Envíe la foto del pesaje:"
        )
    else:
        await message.answer("✅ Totales verificados. Envíe la foto del pesaje:")
    
    await state.set_state(RegistroState.foto)

# ==================== FOTO Y GUARDADO ==================== #
@dp.message(RegistroState.foto, F.photo)
async def guardar_registro(message: types.Message, state: FSMContext):
    try:
        await message.answer("⏳ Procesando foto y guardando registro...")
        
        data = await state.get_data()
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        
        # Crear carpeta para imágenes si no existe
        images_folder = "imagenes_pesajes"
        if not os.path.exists(images_folder):
            os.makedirs(images_folder)
        
        # Nombre único para la imagen
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        placa = data.get('camion', 'SIN_PLACA')
        file_name = f"pesaje_{placa}_{timestamp}.jpg"
        local_file_path = os.path.join(images_folder, file_name)
        
        # Descargar la imagen
        await bot.download_file(file_info.file_path, destination=local_file_path)
        
        # Obtener ruta absoluta para guardar en BD
        absolute_path = os.path.abspath(local_file_path)
        print(f"✅ Imagen guardada localmente: {absolute_path}")
        
        # Intentar subir a Drive (si está configurado)
        drive_link = None
        if GOOGLE_CREDENTIALS_PATH and os.path.exists(GOOGLE_CREDENTIALS_PATH) and GOOGLE_FOLDER_ID:
            drive_link = upload_to_drive(local_file_path, file_name)
            if drive_link:
                print(f"✅ Imagen subida a Google Drive")
        
        # Si no se subió a Drive, usar ruta absoluta local
        if not drive_link:
            drive_link = absolute_path
            print(f"📁 Imagen almacenada localmente en: {absolute_path}")
        
        # Intentar guardar en base de datos (opcional)
        registro_guardado = False
        if DATABASE_URL:
            conn = None
            try:
                conn = await get_db_connection()
                if conn:
                    tipo_pesaje = data.get("tipo")
                    placa = data.get("camion")
                    
                    # Para ORIGEN: guardar peso simple
                    if tipo_pesaje == "Origen":
                        peso_str = str(data.get("peso", "0"))
                        peso_float = float(peso_str.replace(",", "."))
                        bascula = data.get("bascula", "Báscula Origen")

                        await conn.execute('''
                            INSERT INTO registros (camion_id, tipo_pesaje, bascula, peso, fecha, cedula, imagen, tipodeempleado, tipocarga)
                            VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8)
                        ''', placa, tipo_pesaje, bascula, peso_float, data.get("cedula"), drive_link, data.get("tipo_empleado"), data.get("tipo_carga"))
                        
                        registro_guardado = True
                        print("✅ Registro de ORIGEN guardado en base de datos")
                    
                    # Para DESTINO: guardar total de báscula y validar con último origen
                    elif tipo_pesaje == "Destino":
                        peso_bascula = data.get("peso_bascula_general", 0)
                        silos = data.get("silos", [])
                        total_silos = data.get("total_silos", 0)
                        
                        # Buscar el último registro de ORIGEN para esta placa
                        print(f"🔍 Buscando último ORIGEN para placa: {placa}")
                        
                        ultimo_origen = await conn.fetchrow('''
                            SELECT peso FROM registros 
                            WHERE camion_id = $1 AND tipo_pesaje = 'Origen'
                            ORDER BY fecha DESC
                            LIMIT 1
                        ''', placa)
                        print(f"� Resultado de búsqueda: {ultimo_origen}")
                        
                        # Guardar el registro principal (solo peso de báscula)
                        registro_id = await conn.fetchval('''
                            INSERT INTO registros (camion_id, tipo_pesaje, bascula, peso, fecha, cedula, imagen, tipodeempleado, tipocarga)
                            VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8)
                            RETURNING id
                        ''', placa, tipo_pesaje, "Báscula General", peso_bascula, data.get("cedula"), drive_link, data.get("tipo_empleado"), data.get("tipo_carga"))
                        
                        print(f"✅ Registro de DESTINO guardado en base de datos (ID: {registro_id})")
                        print(f"   - Peso báscula: {peso_bascula} kg")
                        print(f"   - Total silos: {total_silos} kg")
                        
                        # Guardar cada silo en la tabla de silos
                        for silo in silos:
                            await conn.execute('''
                                INSERT INTO silos (registro_id, numero_silo, peso, fecha)
                                VALUES ($1, $2, $3, NOW())
                            ''', registro_id, silo['numero'], float(silo['peso']))
                            print(f"   ✅ Silo {silo['numero']}: {silo['peso']} kg guardado")
                        
                        # Validar con último origen y guardar para el resumen
                        mensaje_validacion = ""
                        if ultimo_origen:
                            peso_origen = float(ultimo_origen['peso'])
                            diferencia = peso_bascula - peso_origen
                            diferencia_abs = abs(diferencia)
                            
                            print(f"   - Último peso ORIGEN: {peso_origen} kg")
                            print(f"   - Diferencia: {diferencia_abs} kg")
                            
                            # Guardar info de validación para mostrar al usuario
                            await state.update_data(
                                peso_origen_comparacion=peso_origen,
                                diferencia_origen=diferencia
                            )
                            
                            if diferencia_abs > 1.0:  # Tolerancia de 1 kg
                                print(f"   ⚠️ ADVERTENCIA: Diferencia mayor a 1kg con el último origen")
                        else:
                            print("   ⚠️ No se encontró registro de ORIGEN previo para esta placa")
                    
                else:
                    print("⚠️ No se pudo obtener conexión a la base de datos")
            except Exception as e:
                print(f"⚠️ Error guardando en base de datos: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if conn:
                    await release_db_connection(conn)

        # Recargar data desde state para obtener los datos actualizados (peso_origen_comparacion, etc.)
        data = await state.get_data()
        
        # Crear resumen según el tipo de pesaje
        tipo_pesaje = data.get('tipo')
        
        if tipo_pesaje == "Origen":
            peso_str = data.get('peso', '0')
            resumen = (
                f"✅ Registro completado\n"
                f"👤 Cédula: {data.get('cedula')}\n"
                f"👷 Tipo: {data.get('tipo_empleado')}\n"
                f"🚚 Placa: {data.get('camion')}\n"
                f"📦 Carga: {data.get('tipo_carga')}\n"
                f"⚖️ Pesaje: {tipo_pesaje}\n"
                f"🏋️ Peso: {peso_str} kg\n"
                f"🕒 Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
        else:  # Destino
            silos = data.get('silos', [])
            peso_bascula = data.get('peso_bascula_general', 0)
            total_silos = data.get('total_silos', 0)
            peso_origen_comp = data.get('peso_origen_comparacion')
            diferencia_origen = data.get('diferencia_origen')

            detalle_silos = "\n".join([f"  • Silo {s['numero']}: {s['peso']} kg" for s in silos])

            resumen = (
                f"✅ Registro completado\n"
                f"👤 Cédula: {data.get('cedula')}\n"
                f"👷 Tipo: {data.get('tipo_empleado')}\n"
                f"🚚 Placa: {data.get('camion')}\n"
                f"📦 Carga: {data.get('tipo_carga')}\n"
                f"⚖️ Pesaje: {tipo_pesaje}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📍 Peso Báscula: {peso_bascula} kg\n"
                f"📦 Silos:\n{detalle_silos}\n"
                f"🏋️ Total Descargado: {total_silos} kg\n"
            )
            
            # Agregar comparación con origen si existe
            if peso_origen_comp is not None:
                diferencia_abs = abs(diferencia_origen)
                if diferencia_abs <= 1.0:
                    resumen += f"✅ Origen: {peso_origen_comp} kg (Diferencia: {diferencia_abs:.1f} kg)\n"
                else:
                    if diferencia_origen > 0:
                        resumen += f"⚠️ Origen: {peso_origen_comp} kg (Báscula {diferencia_abs:.1f} kg mayor)\n"
                    else:
                        resumen += f"⚠️ Origen: {peso_origen_comp} kg (Báscula {diferencia_abs:.1f} kg menor)\n"
            else:
                resumen += f"⚠️ Sin registro de origen previo\n"
            
            resumen += f"━━━━━━━━━━━━━━━\n"
            resumen += f"🕒 Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}"

        await message.answer(resumen)
        
        # Enviar notificación al grupo con la FOTO
        if GROUP_CHAT_ID:
            try:
                # Crear mensaje para el grupo según el tipo
                if tipo_pesaje == "Origen":
                    peso_str = data.get('peso', '0')
                    mensaje_grupo = (
                        f"🚨 *NUEVO REGISTRO DE PESAJE*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 *Cédula:* `{data.get('cedula')}`\n"
                        f"👷 *Tipo:* {data.get('tipo_empleado')}\n"
                        f"🚚 *Placa:* `{data.get('camion')}`\n"
                        f"📦 *Carga:* {data.get('tipo_carga')}\n"
                        f"⚖️ *Pesaje:* {tipo_pesaje}\n"
                        f"🏋️ *Peso:* {peso_str} kg\n"
                        f"🕒 *Fecha:* {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                else:  # Destino
                    silos = data.get('silos', [])
                    peso_bascula = data.get('peso_bascula_general', 0)
                    total_silos = data.get('total_silos', 0)
                    peso_origen_comp = data.get('peso_origen_comparacion')
                    diferencia_origen = data.get('diferencia_origen')

                    detalle_silos = "\n".join([f"  • Silo {s['numero']}: {s['peso']} kg" for s in silos])

                    mensaje_grupo = (
                        f"🚨 *NUEVO REGISTRO DE PESAJE*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 *Cédula:* `{data.get('cedula')}`\n"
                        f"👷 *Tipo:* {data.get('tipo_empleado')}\n"
                        f"🚚 *Placa:* `{data.get('camion')}`\n"
                        f"📦 *Carga:* {data.get('tipo_carga')}\n"
                        f"⚖️ *Pesaje:* {tipo_pesaje}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📍 *Peso Báscula:* {peso_bascula} kg\n"
                        f"📦 *Descarga por Silos:*\n{detalle_silos}\n"
                        f"🏋️ *Total Descargado:* {total_silos} kg\n"
                    )
                    
                    # Agregar comparación con origen
                    if peso_origen_comp is not None:
                        diferencia_abs = abs(diferencia_origen)
                        if diferencia_abs <= 1.0:
                            mensaje_grupo += f"✅ *Peso Origen:* {peso_origen_comp} kg (Dif: {diferencia_abs:.1f} kg)\n"
                        else:
                            if diferencia_origen > 0:
                                mensaje_grupo += f"⚠️ *Peso Origen:* {peso_origen_comp} kg (Báscula +{diferencia_abs:.1f} kg)\n"
                            else:
                                mensaje_grupo += f"⚠️ *Peso Origen:* {peso_origen_comp} kg (Báscula -{diferencia_abs:.1f} kg)\n"
                    else:
                        mensaje_grupo += f"⚠️ *Sin registro de origen previo*\n"
                    
                    mensaje_grupo += (
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🕒 *Fecha:* {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                    )
                
                # Enviar la foto con el caption al grupo
                with open(local_file_path, 'rb') as photo_file:
                    await bot.send_photo(
                        chat_id=GROUP_CHAT_ID,
                        photo=types.BufferedInputFile(photo_file.read(), filename=file_name),
                        caption=mensaje_grupo,
                        parse_mode="Markdown"
                    )
                print("✅ Notificación enviada al grupo con foto")
                
            except Exception as e:
                print(f"⚠️ Error al enviar notificación al grupo: {e}")
                # Si falla enviar con foto, intentar solo texto
                try:
                    await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")
                    print("✅ Notificación enviada al grupo (solo texto)")
                except Exception as e2:
                    print(f"⚠️ Error al enviar mensaje de texto al grupo: {e2}")
        
        # Volver al menú principal
        await volver_menu_principal(message, state)
        
    except Exception as e:
        print(f"❌ Error en guardar_registro: {e}")
        await message.answer(f"❌ Error procesando el registro: {e}\nIntente nuevamente con /start")
        await state.clear()

# ==================== HANDLER PARA FOTOS FUERA DE ESTADO ==================== #
@dp.message(RegistroState.foto)
async def foto_sin_imagen(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor envíe una FOTO del pesaje (no texto).")

# ==================== HANDLER DE FALLBACK ==================== #
@dp.message()
async def fallback_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(
            "👋 Hola! Para iniciar un registro de pesaje, use el comando /start\n\n"
            "Si ya inició un registro, continúe con los pasos indicados."
        )
    else:
        await message.answer("⚠️ Por favor siga las instrucciones del paso actual.")

# ==================== MAIN ==================== #
async def main():
    print("\n" + "="*50)
    print("🚀 Iniciando bot de pesajes...")
    print("="*50)
    
    # Inicializar pool de base de datos
    if DATABASE_URL:
        print("📊 Configurando base de datos...")
        await init_db_pool()
    else:
        print("⚠️  Base de datos NO configurada")
    
    # Verificar configuración
    if GOOGLE_CREDENTIALS_PATH and os.path.exists(GOOGLE_CREDENTIALS_PATH):
        print("✅ Credenciales de Google Drive encontradas")
    else:
        print("⚠️  Google Drive NO configurado (las fotos se guardarán localmente)")
    
    print("="*50)
    print("🤖 Bot iniciado correctamente.")
    print("💬 Esperando mensajes...")
    print("="*50 + "\n")
    
    try:
        # Iniciar polling (esto bloquea hasta que se detenga el bot)
        await dp.start_polling(bot)
    finally:
        # Cerrar pool al salir
        global db_pool
        if db_pool:
            print("\n🔒 Cerrando conexiones a la base de datos...")
            await db_pool.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Bot detenido por el usuario (Ctrl+C)")
    except Exception as e:
        print(f"\n\n❌ Error crítico: {e}")
        raise



