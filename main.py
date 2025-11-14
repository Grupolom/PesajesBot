import os
import re
import asyncio
import asyncpg
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
    menu_principal = State()  # Menú inicial
    cedula = State()
    confirmar_cedula = State()
    tipo_empleado = State()  # NUEVO: Tipo de empleado
    confirmar_tipo_empleado = State()  # NUEVO: Confirmar tipo de empleado
    camion = State()
    confirmar_camion = State()
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

# ==================== VALIDACIONES ==================== #
def validar_cedula(valor):
    return valor.isdigit()

def validar_placa(valor):
    return re.fullmatch(r"^[A-Z]{3}\d{3}$", valor.upper())

def validar_peso(valor):
    return re.fullmatch(r"^\d+(,\d+)?$", valor)

async def volver_menu_principal(message: types.Message, state: FSMContext):
    """Función helper para volver al menú principal"""
    await state.clear()
    await message.answer(
        "👋 Bienvenido al sistema de pesajes.\n\n"
        "¿Qué desea hacer?\n\n"
        "1️⃣ Registrar Pesaje\n"
        "2️⃣ Consultar Capacidad de Silos\n"
        "3️⃣ Restar Peso de Silo\n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 en cualquier momento para cancelar_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.menu_principal)

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
    await message.answer(
        "👋 Bienvenido al sistema de pesajes.\n\n"
        "¿Qué desea hacer?\n\n"
        "1️⃣ Registrar Pesaje\n"
        "2️⃣ Consultar Capacidad de Silos\n"
        "3️⃣ Restar Peso de Silo\n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 en cualquier momento para cancelar_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.menu_principal)

# ==================== MENÚ PRINCIPAL ==================== #
@dp.message(RegistroState.menu_principal, F.text == "1")
async def iniciar_registro(message: types.Message, state: FSMContext):
    await message.answer("Por favor, ingrese su cédula:")
    await state.set_state(RegistroState.cedula)

@dp.message(RegistroState.menu_principal, F.text == "2")
async def consultar_silos(message: types.Message, state: FSMContext):
    await message.answer("Ingrese el número del silo que desea consultar:")
    await state.set_state(RegistroState.consulta_silo)

@dp.message(RegistroState.menu_principal, F.text == "3")
async def restar_peso_silo(message: types.Message, state: FSMContext):
    await message.answer("Ingrese el número del silo del cual desea restar peso:")
    await state.set_state(RegistroState.restar_silo_numero)

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
    await message.answer(
        "\n¿Desea hacer algo más?\n\n"
        "1️⃣ Registrar Pesaje\n"
        "2️⃣ Consultar Capacidad de Silos\n"
        "3️⃣ Restar Peso de Silo\n\n"
        "Escriba el número de la opción:"
    )
    await state.set_state(RegistroState.menu_principal)

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
    await message.answer(
        "\n¿Desea hacer algo más?\n\n"
        "1️⃣ Registrar Pesaje\n"
        "2️⃣ Consultar Capacidad de Silos\n"
        "3️⃣ Restar Peso de Silo\n\n"
        "Escriba el número de la opción:"
    )
    await state.set_state(RegistroState.menu_principal)

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
    builder.button(text="Origen")
    builder.button(text="Destino")
    builder.adjust(2)
    await message.answer("Seleccione el tipo de pesaje (Origen o Destino):", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(RegistroState.tipo)

@dp.message(RegistroState.confirmar_camion, F.text == "2")
async def editar_camion(message: types.Message, state: FSMContext):
    await message.answer("Ingrese la placa del camión nuevamente:")
    await state.set_state(RegistroState.camion)

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
                            INSERT INTO registros (camion_id, tipo_pesaje, bascula, peso, fecha, cedula, imagen, tipodeempleado)
                            VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7)
                        ''', placa, tipo_pesaje, bascula, peso_float, data.get("cedula"), drive_link, data.get("tipo_empleado"))
                        
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
                            INSERT INTO registros (camion_id, tipo_pesaje, bascula, peso, fecha, cedula, imagen, tipodeempleado)
                            VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7)
                            RETURNING id
                        ''', placa, tipo_pesaje, "Báscula General", peso_bascula, data.get("cedula"), drive_link, data.get("tipo_empleado"))
                        
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
        await message.answer(
            "\n¿Desea hacer algo más?\n\n"
            "1️⃣ Registrar Pesaje\n"
            "2️⃣ Consultar Capacidad de Silos\n"
            "3️⃣ Restar Peso de Silo\n\n"
            "Escriba el número de la opción:"
        )
        await state.set_state(RegistroState.menu_principal)
        
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



