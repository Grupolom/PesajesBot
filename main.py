import os
import re
import asyncio
import asyncpg
import uuid
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardBuilder
from aiogram.types import ReplyKeyboardRemove
from dotenv import load_dotenv
from datetime import datetime, timedelta

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

    # Estados para Registro de consumo por lote (antes Registro de Animales)
    sitio3_cedula = State()
    sitio3_confirmar_cedula = State()
    sitio3_numero_banda = State()
    sitio3_rango_corrales = State()
    sitio3_tipo_comida = State()
    sitio3_resumen_confirmacion = State()  # Confirmación final con resumen
    sitio3_editar_opcion = State()  # Para editar una opción específica
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

    # Estados para Ingreso concentrado en silo (antes Medición de Silos)
    medicion_cedula = State()
    medicion_confirmar_cedula = State()
    medicion_seleccion_silos = State()
    medicion_confirmar_silos = State()
    medicion_tipo_comida = State()
    medicion_confirmar_tipo_comida = State()
    medicion_peso_descargue = State()  # Cambiado: ahora es peso de descargue en kilos
    medicion_confirmar_peso_descargue = State()
    medicion_foto_factura = State()  # Cambiado: ahora es foto de factura
    medicion_agregar_mas = State()

    # Estados para Registro de Celdas de Carga (Opción 4)
    celdas_cedula = State()
    celdas_confirmar_cedula = State()
    celdas_seleccion_silo = State()
    celdas_confirmar_silo = State()
    celdas_saldo = State()
    celdas_confirmar_saldo = State()
    celdas_foto = State()
    celdas_agregar_mas = State()

    # Estados para Registro de Combustible (Opción 5)
    combustible_cedula = State()
    combustible_confirmar_cedula = State()
    combustible_tipo = State()
    combustible_confirmar_tipo = State()
    combustible_equipo = State()  # Planta 1, Planta 2, Can-am, Vehiculos, Equipos
    combustible_confirmar_equipo = State()
    combustible_placa = State()  # Solo si eligió Vehículos
    combustible_confirmar_placa = State()
    combustible_nombre_equipo = State()  # Solo si eligió Equipos u Otros
    combustible_confirmar_nombre_equipo = State()
    combustible_centro_costo = State()
    combustible_confirmar_centro_costo = State()

    # Estados para Traslado entre Corrales (Opción 6)
    traslado_cedula = State()
    traslado_confirmar_cedula = State()
    traslado_corral_origen = State()
    traslado_confirmar_origen = State()
    traslado_corral_destino = State()
    traslado_confirmar_destino = State()

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
    
    # Flujo Peso Vacío
    peso_vacio_tipo_carga = State()  # Pregunta qué entregó o va a cargar

# ==================== ESTADOS PARA OPERARIO SITIO 1 ==================== #
class OperarioSitio1State(StatesGroup):
    """Estados para el menú de Operario Sitio 1 (Granja)"""
    cedula = State()
    confirmar_cedula = State()

    cantidad_lechones = State()  # Cuantos lechones va a pesar
    confirmar_cantidad_lechones = State()

    cantidad_pesajes = State()
    confirmar_cantidad_pesajes = State()

    lechones_por_pesaje = State()
    confirmar_lechones_por_pesaje = State()

    # Estados para el loop de pesaje
    peso_pesaje = State()  # Peso del pesaje actual
    confirmar_peso = State()  # Confirmar peso del pesaje
    foto_final = State()  # Única foto de confirmación al final

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

def validar_numero_banda(valor: str) -> tuple[bool, str, str]:
    """
    Valida número de banda: acepta cualquier texto (números, letras, guiones, etc.)
    Retorna: (es_valido, banda, mensaje_error)
    """
    valor = valor.strip()

    if not valor:
        return False, "", "Debe ingresar un número de banda"

    if len(valor) > 50:
        return False, "", "El número de banda no puede superar 50 caracteres"

    return True, valor, ""

def validar_rango_corrales(valor: str, max_rango: int = None) -> tuple[bool, str]:
    """
    Valida rango de corrales: formato X-Y donde X <= Y
    max_rango: Si se especifica, el rango no puede superar este valor
    Retorna: (es_valido, mensaje_error)
    """
    # Validar formato con regex
    if not re.match(r'^\d+-\d+$', valor):
        return False, "Formato incorrecto. Use: número-número (ejemplo: 1-10)"

    # Extraer números
    partes = valor.split('-')
    try:
        inicio = int(partes[0])
        fin = int(partes[1])

        if inicio < 1:
            return False, "El número de corral inicial debe ser al menos 1 (no puede ser 0)"

        if fin < 1:
            return False, "El número de corral final debe ser al menos 1"

        if inicio > fin:
            return False, f"El número inicial ({inicio}) debe ser menor o igual al final ({fin})"

        # Validar rango máximo si se especifica
        if max_rango is not None:
            rango_actual = fin - inicio + 1
            if rango_actual > max_rango:
                return False, f"El rango máximo permitido es de {max_rango} corrales. Usted ingresó {rango_actual} corrales."

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

# ==================== VALIDACIONES MEDICIÓN DE SILOS ==================== #

def validar_seleccion_silos(valor: str) -> tuple[bool, list[int], str]:
    """
    Valida selección de silos: números del 1 al 6 separados por comas
    Retorna: (es_valido, lista_silos, mensaje_error)
    """
    # Limpiar espacios
    valor_limpio = valor.replace(" ", "")

    # Validar formato básico
    if not re.match(r'^[1-6](,[1-6])*$', valor_limpio):
        return False, [], "Formato incorrecto. Use números del 1 al 6 separados por comas (ej: 1,3,5)"

    # Extraer números
    try:
        silos = [int(s) for s in valor_limpio.split(',')]

        # Verificar duplicados
        if len(silos) != len(set(silos)):
            duplicados = [s for s in set(silos) if silos.count(s) > 1]
            return False, [], f"Silos duplicados detectados: {', '.join(map(str, duplicados))}"

        # Ordenar silos
        silos_ordenados = sorted(silos)

        return True, silos_ordenados, ""

    except ValueError:
        return False, [], "Error al procesar los números de silos"

def validar_peso_toneladas(valor: str) -> tuple[bool, float, str]:
    """
    Valida peso en toneladas: decimal positivo, 0-50 toneladas
    Retorna: (es_valido, peso, mensaje_error)
    """
    # Reemplazar coma por punto para decimales
    valor_normalizado = valor.replace(",", ".")

    try:
        peso = float(valor_normalizado)

        if peso < 0:
            return False, 0.0, "El peso no puede ser negativo"

        if peso > 50:
            return False, 0.0, "El peso no puede superar 50 toneladas (límite de capacidad)"

        # Redondear a 2 decimales
        peso = round(peso, 2)

        return True, peso, ""

    except ValueError:
        return False, 0.0, "Debe ingresar un número válido (use punto o coma para decimales)"

# ==================== SISTEMA DE ALERTAS DE SEGURIDAD ==================== #

async def verificar_multiples_cedulas(telegram_user_id: int, cedula_actual: str) -> tuple[bool, list[str]]:
    """
    Verifica si un telegram_user_id ha usado diferentes cédulas previamente en TODAS las tablas.

    Args:
        telegram_user_id: ID del usuario de Telegram
        cedula_actual: Cédula que acaba de ingresar

    Returns:
        (hay_alerta, lista_cedulas_diferentes)
    """
    conn = None
    cedulas_encontradas = set()

    try:
        conn = await get_db_connection()
        if not conn:
            print("⚠️ No se pudo verificar múltiples cédulas (sin conexión a BD)")
            return False, []

        # Consultar en tabla de Registro de Animales (Sitio 3)
        registros_animales = await conn.fetch('''
            SELECT DISTINCT cedula_operario
            FROM operario_sitio3_animales
            WHERE telegram_user_id = $1
            AND cedula_operario != $2
        ''', telegram_user_id, cedula_actual)

        for reg in registros_animales:
            cedulas_encontradas.add(reg['cedula_operario'])

        # Consultar en tabla de Descarga de Animales (Sitio 3)
        registros_descarga = await conn.fetch('''
            SELECT DISTINCT cedula_operario
            FROM operario_sitio3_descarga_animales
            WHERE telegram_user_id = $1
            AND cedula_operario != $2
        ''', telegram_user_id, cedula_actual)

        for reg in registros_descarga:
            cedulas_encontradas.add(reg['cedula_operario'])

        # Consultar en tabla de Conductores
        try:
            registros_conductores = await conn.fetch('''
                SELECT DISTINCT cedula
                FROM conductores
                WHERE telegram_user_id = $1
                AND cedula != $2
            ''', telegram_user_id, cedula_actual)

            for reg in registros_conductores:
                cedulas_encontradas.add(reg['cedula'])
        except Exception as e:
            print(f"⚠️ Tabla conductores no existe o error: {e}")

        # Consultar en tabla de Operario Sitio 1 (Granja)
        try:
            registros_sitio1 = await conn.fetch('''
                SELECT DISTINCT cedula
                FROM operario_fijo_granja
                WHERE telegram_user_id = $1
                AND cedula != $2
            ''', telegram_user_id, cedula_actual)

            for reg in registros_sitio1:
                cedulas_encontradas.add(reg['cedula'])
        except Exception as e:
            print(f"⚠️ Tabla operario_fijo_granja no existe o error: {e}")

        # Si encontramos otras cédulas, hay alerta
        if cedulas_encontradas:
            print(f"🚨 ALERTA: Usuario {telegram_user_id} ha usado múltiples cédulas:")
            print(f"   - Cédula actual: {cedula_actual}")
            print(f"   - Cédulas previas: {', '.join(sorted(cedulas_encontradas))}")
            return True, sorted(list(cedulas_encontradas))

        return False, []

    except Exception as e:
        print(f"❌ Error en verificación de múltiples cédulas: {e}")
        import traceback
        traceback.print_exc()
        return False, []

    finally:
        if conn:
            await release_db_connection(conn)

async def enviar_alerta_seguridad(
    telegram_user_id: int,
    username: str,
    cedula_actual: str,
    cedulas_previas: list[str],
    tipo_operacion: str
):
    """
    Envía alerta de seguridad al grupo de Telegram cuando se detectan múltiples cédulas.

    Args:
        telegram_user_id: ID del usuario de Telegram
        username: Nombre de usuario de Telegram (@username o nombre completo)
        cedula_actual: Cédula que acaba de usar
        cedulas_previas: Lista de cédulas diferentes usadas anteriormente
        tipo_operacion: "Registro de Animales" o "Descarga de Animales"
    """
    if not GROUP_CHAT_ID:
        print("⚠️ No se puede enviar alerta (GROUP_CHAT_ID no configurado)")
        return

    try:
        fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        # Formatear lista de cédulas previas
        cedulas_previas_texto = '\n'.join([f"   • `{c}`" for c in cedulas_previas])

        mensaje_alerta = (
            "🚨 *ALERTA DE SEGURIDAD - MÚLTIPLES CÉDULAS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ Se ha detectado que un mismo usuario\n"
            "de Telegram ha usado diferentes cédulas.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 *DATOS DEL USUARIO:*\n\n"
            f"• Usuario Telegram: {username}\n"
            f"• ID Telegram: `{telegram_user_id}`\n"
            f"• Operación: {tipo_operacion}\n"
            f"• Fecha/Hora: {fecha_hora}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🆔 *CÉDULAS DETECTADAS:*\n\n"
            f"• Cédula ACTUAL: `{cedula_actual}`\n\n"
            f"• Cédulas PREVIAS:\n{cedulas_previas_texto}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ *ACCIÓN REQUERIDA:*\n"
            "Por favor verificar la identidad del operario\n"
            "y tomar las medidas necesarias.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        await bot.send_message(GROUP_CHAT_ID, mensaje_alerta, parse_mode="Markdown")
        print(f"✅ Alerta de seguridad enviada al grupo (User ID: {telegram_user_id})")

    except Exception as e:
        print(f"❌ Error enviando alerta de seguridad: {e}")
        import traceback
        traceback.print_exc()

# ==================== FIN SISTEMA DE ALERTAS ==================== #

async def volver_menu_principal(message: types.Message, state: FSMContext):
    """Función helper para volver al menú principal multi-perfil"""
    await state.clear()
    await message.answer(
        "👋 *Bienvenido al Sistema de Gestión de Lomarosa*\n\n"
        "Seleccione su perfil:\n\n"
        "1️⃣ Operario Sitio 3\n"
        "2️⃣ Operario Sitio 1\n"
        "3️⃣ Conductores\n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 en cualquier momento para cancelar_\n\n"
        "🐷 *LOMAROSA* 🐷\n"
        "_Campo bien hecho, cerdos bien criados_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.menu_principal)

async def volver_menu_sitio3(message: types.Message, state: FSMContext):
    """Función helper para volver al submenú de Operario Sitio 3"""
    await message.answer(
        "🐷 *OPERARIO SITIO 3*\n\n"
        "Seleccione una opción:\n\n"
        "1️⃣ Registro de consumo por lote\n"
        "2️⃣ Ingreso concentrado en silo\n"
        "3️⃣ Ingreso de lechones\n"
        "4️⃣ Registro celdas de carga\n"
        "5️⃣ Registro de combustible\n"
        "6️⃣ Traslado entre corrales\n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 para volver al menú principal_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_menu)

async def finalizar_flujo(message: types.Message, state: FSMContext):
    """Función para finalizar el flujo y despedir al usuario (NO vuelve al menú)"""
    await state.clear()
    await message.answer(
        "✅ *FINALIZADO*\n\n"
        "Has acabado el flujo y el registro fue exitoso.\n\n"
        "En caso de volver a querer usar el bot, escriba:\n"
        "/start\n\n"
        "Si no, ¡hasta luego!\n\n"
        "🙏 *MUCHAS GRACIAS*\n\n"
        "🐷 *LOMAROSA* 🐷\n"
        "_Campo bien hecho, cerdos bien criados_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

# ==================== CONFIGURAR BOT ==================== #
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== SISTEMA DE TIMEOUT DE INACTIVIDAD ==================== #
# Diccionario para rastrear la última actividad de cada usuario
user_last_activity = {}
TIMEOUT_MINUTES = 20

async def guardar_registro_inactivo(user_id: int, state_name: str, data: dict):
    """Guarda un registro parcial en la base de datos con estado INACTIVO"""
    conn = None
    try:
        conn = await get_db_connection()
        if not conn:
            print(f"⚠️ No se pudo guardar registro inactivo para user {user_id}")
            return

        # Determinar en qué tabla guardar según el estado
        fecha_hora = datetime.now()

        if "ConductoresState" in state_name:
            # Guardar en tabla conductores con estado INACTIVO
            await conn.execute('''
                INSERT INTO conductores (
                    telegram_id, cedula, placa, tipo_carga, num_animales, tipo_combustible,
                    cantidad_galones, factura_dato1, factura_dato2, factura_dato3,
                    factura_foto, bascula, cerdos_vivos, cerdos_muertos, peso, foto_pesaje, fecha
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ''',
                user_id,
                data.get('cedula', 'INACTIVO'),
                data.get('placa', 'INACTIVO'),
                data.get('tipo_carga', 'INACTIVO'),
                data.get('num_animales'),
                data.get('tipo_combustible'),
                data.get('cantidad_galones'),
                data.get('numero_factura'),
                data.get('tipo_alimento'),
                data.get('kilos_comprados'),
                data.get('factura_foto'),
                data.get('bascula', 'INACTIVO'),
                data.get('cerdos_vivos'),
                data.get('cerdos_muertos'),
                data.get('peso', 0.0),
                data.get('foto_pesaje'),
                fecha_hora
            )
            print(f"✅ Registro INACTIVO guardado en conductores para user {user_id}")

        elif "OperarioSitio1State" in state_name:
            # Guardar en tabla operario_fijo_granja
            import json
            pesos = data.get("pesos", [])
            fotos = data.get("fotos", [])
            peso_total = sum(pesos) if pesos else 0
            peso_promedio = peso_total / len(pesos) if pesos else 0

            await conn.execute('''
                INSERT INTO operario_fijo_granja (
                    telegram_id, cedula, cantidad_lechones, peso_total, peso_promedio,
                    pesos_detalle, fotos_urls, fecha
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ''',
                user_id,
                data.get('cedula', 'INACTIVO'),
                data.get('cantidad_lechones', 0),
                peso_total,
                peso_promedio,
                json.dumps(pesos),
                json.dumps(fotos),
                fecha_hora
            )
            print(f"✅ Registro INACTIVO guardado en operario_fijo_granja para user {user_id}")

        elif "sitio3" in state_name.lower() or "RegistroState" in state_name:
            # Para Sitio 3, guardar según el tipo de operación
            if "medicion" in state_name.lower():
                await conn.execute('''
                    INSERT INTO operario_sitio3_medicion_silos (
                        cedula_operario, silos_medidos, tipo_comida, peso_antes, imagen_antes,
                        peso_despues, imagen_despues, diferencia, fecha_registro, session_id, telegram_user_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ''',
                    data.get('medicion_cedula', 'INACTIVO'),
                    data.get('medicion_silos_seleccionados', 'INACTIVO'),
                    data.get('medicion_tipo_comida', 'INACTIVO'),
                    data.get('medicion_peso_antes'),
                    data.get('medicion_imagen_antes'),
                    data.get('medicion_peso_despues'),
                    data.get('medicion_imagen_despues'),
                    0.0,
                    fecha_hora,
                    data.get('medicion_session_id', str(uuid.uuid4())),
                    user_id
                )
            else:
                # Registro de animales o descarga
                await conn.execute('''
                    INSERT INTO operario_sitio3_animales (
                        cedula_operario, cantidad_animales, rango_corrales, tipo_comida,
                        fecha_registro, session_id, telegram_user_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''',
                    data.get('sitio3_cedula', 'INACTIVO'),
                    data.get('sitio3_cantidad_animales', 0),
                    data.get('sitio3_rango_corrales', 'INACTIVO'),
                    data.get('sitio3_tipo_comida', 'INACTIVO'),
                    fecha_hora,
                    data.get('session_id', str(uuid.uuid4())),
                    user_id
                )
            print(f"✅ Registro INACTIVO guardado en Sitio 3 para user {user_id}")

    except Exception as e:
        print(f"⚠️ Error guardando registro inactivo: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

@dp.update.middleware()
async def timeout_middleware(handler, event, data):
    """Middleware para detectar inactividad de 20 minutos"""
    # Solo aplicar a mensajes de usuarios
    if hasattr(event, 'from_user') and event.from_user:
        user_id = event.from_user.id
        current_time = datetime.now()

        # Verificar si el usuario tiene actividad previa
        if user_id in user_last_activity:
            last_activity = user_last_activity[user_id]
            time_diff = current_time - last_activity

            # Si han pasado más de 20 minutos
            if time_diff > timedelta(minutes=TIMEOUT_MINUTES):
                state = data.get("state")
                if state:
                    current_state = await state.get_state()

                    # Solo guardar si hay un estado activo (no en menú principal)
                    if current_state and current_state != "RegistroState:menu_principal":
                        state_data = await state.get_data()

                        # Guardar registro parcial
                        await guardar_registro_inactivo(user_id, current_state, state_data)

                        # Notificar al usuario
                        await event.answer(
                            "⏱️ *SESIÓN EXPIRADA POR INACTIVIDAD*\n\n"
                            "Han pasado más de 20 minutos sin actividad.\n"
                            "Su progreso ha sido guardado como INACTIVO.\n\n"
                            "Para comenzar de nuevo, use /start",
                            parse_mode="Markdown"
                        )

                        # Limpiar el estado
                        await state.clear()

                        # Remover del diccionario
                        del user_last_activity[user_id]

                        # No continuar con el handler
                        return

        # Actualizar última actividad
        user_last_activity[user_id] = current_time

    # Continuar con el handler normal
    return await handler(event, data)

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
        "👋 *Bienvenido al Sistema de Gestión de Lomarosa*\n\n"
        "Seleccione su perfil:\n\n"
        "1️⃣ Operario Sitio 3\n"
        "2️⃣ Operario Sitio 1\n"
        "3️⃣ Conductores\n\n"
        "Escriba el número de la opción:\n\n"
        "💡 _Escriba 0 en cualquier momento para cancelar_\n\n"
        "🐷 *LOMAROSA* 🐷\n"
        "_Campo bien hecho, cerdos bien criados_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.menu_principal)

# ==================== COMANDOS DE REPORTES/CONSULTAS ==================== #

def formatear_fecha(fecha_obj):
    """Helper para formatear fechas que pueden ser datetime o string"""
    try:
        if fecha_obj:
            # Si es datetime, usar strftime
            if hasattr(fecha_obj, 'strftime'):
                return fecha_obj.strftime('%d/%m %H:%M')
            else:
                # Si es string, retornar directamente
                return str(fecha_obj)
        return "Sin fecha"
    except Exception as e:
        print(f"Error formateando fecha: {e}")
        return "Error en fecha"

@dp.message(Command("ayuda"))
async def ayuda(message: types.Message):
    """Muestra todos los comandos disponibles"""
    mensaje = (
        "📚 *COMANDOS DISPONIBLES*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "🏠 *MENÚ PRINCIPAL*\n"
        "`/start` - Iniciar el bot y ver menú principal\n"
        "`/ayuda` - Ver esta lista de comandos\n\n"

        "📊 *REPORTES Y CONSULTAS*\n\n"

        "*Ver registros de hoy:*\n"
        "`/reporte_hoy`\n"
        "Muestra todos los registros del día actual\n\n"

        "*Buscar por cédula:*\n"
        "`/reporte_cedula 1234567890`\n"
        "Muestra todos los registros de una cédula\n"
        "_Reemplaza 1234567890 con la cédula a buscar_\n\n"

        "*Reportes por sitio:*\n"
        "`/reporte_sitio3` - Últimos 10 registros Sitio 3\n"
        "`/reporte_sitio1` - Últimos 10 registros Sitio 1\n"
        "`/reporte_conductores` - Últimos 10 registros Conductores\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *NOTA:* Los comandos se escriben directamente en el chat\n\n"

        "🐷 *LOMAROSA* 🐷\n"
        "_Campo bien hecho, cerdos bien criados_"
    )

    await message.answer(mensaje, parse_mode="Markdown")

@dp.message(Command("reporte_hoy"))
async def reporte_hoy(message: types.Message):
    """Muestra todos los registros del día actual"""
    conn = None
    try:
        conn = await get_db_connection()
        if not conn:
            await message.answer("⚠️ Error de conexión a la base de datos.")
            return

        # Fecha como objeto date (no string)
        fecha_hoy = datetime.now().date()

        # Consultar todas las tablas
        sitio3_animales = await conn.fetch(
            "SELECT cedula_operario, bandas, rango_corrales, tipo_comida, fecha_registro FROM operario_sitio3_animales WHERE DATE(fecha_registro) = $1 ORDER BY fecha_registro DESC",
            fecha_hoy
        )

        sitio1 = await conn.fetch(
            "SELECT cedula, cantidad_lechones, lechones_por_grupo, peso_total, peso_promedio, fecha FROM operario_fijo_granja WHERE DATE(fecha) = $1 ORDER BY fecha DESC",
            fecha_hoy
        )

        conductores = await conn.fetch(
            "SELECT cedula, placa, tipo_carga, bascula, peso, fecha FROM conductores WHERE DATE(fecha) = $1 ORDER BY fecha DESC",
            fecha_hoy
        )

        # Construir mensaje
        mensaje = f"📊 *REPORTE DEL DÍA {datetime.now().strftime('%d/%m/%Y')}*\n\n"

        if sitio3_animales:
            mensaje += f"🐷 *SITIO 3 - ANIMALES* ({len(sitio3_animales)} registros)\n"
            for reg in sitio3_animales[:5]:  # Máximo 5
                mensaje += f"• Cédula: {reg['cedula_operario']} | Banda: {reg['bandas']} | Corrales: {reg['rango_corrales']}\n"
            if len(sitio3_animales) > 5:
                mensaje += f"_... y {len(sitio3_animales) - 5} más_\n"
            mensaje += "\n"

        if sitio1:
            mensaje += f"🐷 *SITIO 1 - LECHONES* ({len(sitio1)} registros)\n"
            for reg in sitio1[:5]:
                mensaje += f"• Cédula: {reg['cedula']} | Lechones: {reg['cantidad_lechones']} | Peso: {reg['peso_total']:.2f} kg\n"
            if len(sitio1) > 5:
                mensaje += f"_... y {len(sitio1) - 5} más_\n"
            mensaje += "\n"

        if conductores:
            mensaje += f"🚛 *CONDUCTORES* ({len(conductores)} registros)\n"
            for reg in conductores[:5]:
                mensaje += f"• Cédula: {reg['cedula']} | Placa: {reg['placa']} | Carga: {reg['tipo_carga']}\n"
            if len(conductores) > 5:
                mensaje += f"_... y {len(conductores) - 5} más_\n"
            mensaje += "\n"

        if not sitio3_animales and not sitio1 and not conductores:
            mensaje += "No hay registros para el día de hoy."

        mensaje += f"\n🐷 *LOMAROSA* 🐷\n_Campo bien hecho, cerdos bien criados_"

        await message.answer(mensaje, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"⚠️ Error al generar reporte: {e}")
        print(f"Error en reporte_hoy: {e}")
    finally:
        if conn:
            await release_db_connection(conn)

@dp.message(Command("reporte_cedula"))
async def reporte_cedula(message: types.Message):
    """Muestra registros de una cédula específica: /reporte_cedula 1234567890"""
    try:
        # Extraer la cédula del comando
        args = message.text.split()
        if len(args) < 2:
            await message.answer(
                "⚠️ *Uso correcto:*\n"
                "`/reporte_cedula 1234567890`\n\n"
                "Ejemplo: `/reporte_cedula 12345678`",
                parse_mode="Markdown"
            )
            return

        cedula = args[1].strip()

        conn = None
        conn = await get_db_connection()
        if not conn:
            await message.answer("⚠️ Error de conexión a la base de datos.")
            return

        # Consultar todas las tablas
        sitio3_animales = await conn.fetch(
            "SELECT bandas, rango_corrales, tipo_comida, fecha_registro FROM operario_sitio3_animales WHERE cedula_operario = $1 ORDER BY fecha_registro DESC LIMIT 10",
            cedula
        )

        sitio1 = await conn.fetch(
            "SELECT cantidad_lechones, lechones_por_grupo, peso_total, peso_promedio, fecha FROM operario_fijo_granja WHERE cedula = $1 ORDER BY fecha DESC LIMIT 10",
            cedula
        )

        conductores = await conn.fetch(
            "SELECT placa, tipo_carga, bascula, peso, fecha FROM conductores WHERE cedula = $1 ORDER BY fecha DESC LIMIT 10",
            cedula
        )

        # Construir mensaje
        mensaje = f"📋 *REPORTE - CÉDULA {cedula}*\n\n"

        if sitio3_animales:
            mensaje += f"🐷 *SITIO 3 - ANIMALES* (últimos {len(sitio3_animales)})\n"
            for reg in sitio3_animales:
                fecha = formatear_fecha(reg['fecha_registro'])
                mensaje += f"• {fecha} | Banda: {reg['bandas']} | Corrales: {reg['rango_corrales']}\n"
            mensaje += "\n"

        if sitio1:
            mensaje += f"🐷 *SITIO 1 - LECHONES* (últimos {len(sitio1)})\n"
            for reg in sitio1:
                fecha = formatear_fecha(reg['fecha'])
                mensaje += f"• {fecha} | {reg['cantidad_lechones']} lechones | {reg['peso_total']:.2f} kg\n"
            mensaje += "\n"

        if conductores:
            mensaje += f"🚛 *CONDUCTORES* (últimos {len(conductores)})\n"
            for reg in conductores:
                fecha = formatear_fecha(reg['fecha'])
                mensaje += f"• {fecha} | {reg['placa']} | {reg['tipo_carga']}\n"
            mensaje += "\n"

        if not sitio3_animales and not sitio1 and not conductores:
            mensaje += f"No se encontraron registros para la cédula {cedula}."

        mensaje += f"\n🐷 *LOMAROSA* 🐷\n_Campo bien hecho, cerdos bien criados_"

        await message.answer(mensaje, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"⚠️ Error al generar reporte: {e}")
        print(f"Error en reporte_cedula: {e}")
    finally:
        if conn:
            await release_db_connection(conn)

@dp.message(Command("reporte_sitio3"))
async def reporte_sitio3(message: types.Message):
    """Muestra últimos 10 registros de Sitio 3"""
    conn = None
    try:
        conn = await get_db_connection()
        if not conn:
            await message.answer("⚠️ Error de conexión a la base de datos.")
            return

        registros = await conn.fetch(
            "SELECT cedula_operario, bandas, rango_corrales, tipo_comida, fecha_registro FROM operario_sitio3_animales ORDER BY fecha_registro DESC LIMIT 10"
        )

        mensaje = "📊 *REPORTE SITIO 3 - ÚLTIMOS 10 REGISTROS*\n\n"

        if registros:
            for i, reg in enumerate(registros, 1):
                fecha = formatear_fecha(reg['fecha_registro'])
                mensaje += (
                    f"{i}. {fecha}\n"
                    f"   • Cédula: {reg['cedula_operario']}\n"
                    f"   • Banda: {reg['bandas']}\n"
                    f"   • Corrales: {reg['rango_corrales']}\n"
                    f"   • Comida: {reg['tipo_comida']}\n\n"
                )
        else:
            mensaje += "No hay registros en Sitio 3."

        mensaje += f"\n🐷 *LOMAROSA* 🐷\n_Campo bien hecho, cerdos bien criados_"

        await message.answer(mensaje, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"⚠️ Error al generar reporte: {e}")
        print(f"Error en reporte_sitio3: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

@dp.message(Command("reporte_sitio1"))
async def reporte_sitio1(message: types.Message):
    """Muestra últimos 10 registros de Sitio 1"""
    conn = None
    try:
        conn = await get_db_connection()
        if not conn:
            await message.answer("⚠️ Error de conexión a la base de datos.")
            return

        registros = await conn.fetch(
            "SELECT cedula, cantidad_lechones, lechones_por_grupo, peso_total, peso_promedio, fecha FROM operario_fijo_granja ORDER BY fecha DESC LIMIT 10"
        )

        mensaje = "📊 *REPORTE SITIO 1 - ÚLTIMOS 10 REGISTROS*\n\n"

        if registros:
            for i, reg in enumerate(registros, 1):
                fecha = reg['fecha'].strftime('%d/%m %H:%M')
                mensaje += (
                    f"{i}. {fecha}\n"
                    f"   • Cédula: {reg['cedula']}\n"
                    f"   • Lechones: {reg['cantidad_lechones']}\n"
                    f"   • Agrupación: {reg['lechones_por_grupo'] or 'N/A'}\n"
                    f"   • Peso total: {reg['peso_total']:.2f} kg\n"
                    f"   • Promedio: {reg['peso_promedio']:.2f} kg\n\n"
                )
        else:
            mensaje += "No hay registros en Sitio 1."

        mensaje += f"\n🐷 *LOMAROSA* 🐷\n_Campo bien hecho, cerdos bien criados_"

        await message.answer(mensaje, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"⚠️ Error al generar reporte: {e}")
        print(f"Error en reporte_sitio1: {e}")
    finally:
        if conn:
            await release_db_connection(conn)

@dp.message(Command("reporte_conductores"))
async def reporte_conductores(message: types.Message):
    """Muestra últimos 10 registros de Conductores"""
    conn = None
    try:
        conn = await get_db_connection()
        if not conn:
            await message.answer("⚠️ Error de conexión a la base de datos.")
            return

        registros = await conn.fetch(
            "SELECT cedula, placa, tipo_carga, bascula, peso, fecha FROM conductores ORDER BY fecha DESC LIMIT 10"
        )

        mensaje = "📊 *REPORTE CONDUCTORES - ÚLTIMOS 10 REGISTROS*\n\n"

        if registros:
            for i, reg in enumerate(registros, 1):
                fecha = reg['fecha'].strftime('%d/%m %H:%M')
                mensaje += (
                    f"{i}. {fecha}\n"
                    f"   • Cédula: {reg['cedula']}\n"
                    f"   • Placa: {reg['placa']}\n"
                    f"   • Carga: {reg['tipo_carga']}\n"
                    f"   • Báscula: {reg['bascula']}\n"
                    f"   • Peso: {reg['peso']:.2f} kg\n\n"
                )
        else:
            mensaje += "No hay registros de Conductores."

        mensaje += f"\n🐷 *LOMAROSA* 🐷\n_Campo bien hecho, cerdos bien criados_"

        await message.answer(mensaje, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"⚠️ Error al generar reporte: {e}")
        print(f"Error en reporte_conductores: {e}")
    finally:
        if conn:
            await release_db_connection(conn)

# ==================== MENÚ PRINCIPAL MULTI-PERFIL ==================== #
@dp.message(RegistroState.menu_principal, F.text == "1")
async def menu_operario_sitio3(message: types.Message, state: FSMContext):
    """Opción 1: Menú Operario Sitio 3"""
    await volver_menu_sitio3(message, state)

@dp.message(RegistroState.menu_principal, F.text == "2")
async def menu_operario_sitio1(message: types.Message, state: FSMContext):
    """Opción 2: Operario Sitio 1 - Registro de Lechones"""
    # Guardar telegram_id automáticamente
    telegram_id = message.from_user.id
    await state.update_data(telegram_id=telegram_id)
    
    await message.answer(
        "🐷 *OPERARIO SITIO 1 - REGISTRO DE LECHONES*\n\n"
        "Por favor, ingrese su *cédula*:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(OperarioSitio1State.cedula)

@dp.message(RegistroState.menu_principal, F.text == "3")
async def menu_conductores(message: types.Message, state: FSMContext):
    """Opción 3: Conductores - Nuevo flujo de pesajes"""
    await state.clear()
    # Guardar telegram_id automáticamente
    await state.update_data(telegram_id=message.from_user.id)
    
    # Mostrar menú de opciones primero
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text="1. Lechones")
    keyboard.button(text="2. Concentrado")
    keyboard.button(text="3. Cerdos Gordos")
    keyboard.button(text="4. Combustible")
    keyboard.button(text="5. Peso Vacío")
    keyboard.adjust(2, 2, 1)
    
    await message.answer(
        "🚛 *CONDUCTORES - REGISTRO DE PESAJE*\n\n"
        "¿Qué va a transportar?\n\n"
        "1️⃣ Lechones (cerdos pequeños)\n"
        "2️⃣ Concentrado (alimento)\n"
        "3️⃣ Cerdos Gordos (para venta)\n"
        "4️⃣ Combustible (diesel/corriente)\n"
        "5️⃣ Peso Vacío\n\n"
        "Seleccione una opción:",
        reply_markup=keyboard.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )
    await state.set_state(ConductoresState.menu_conductores)

# Handler para selección del menú de conductores
@dp.message(ConductoresState.menu_conductores)
async def procesar_menu_conductores(message: types.Message, state: FSMContext):
    """Procesa la selección del menú de conductores"""
    texto = message.text.strip().lower()
    
    # Opciones 1-4: Guardar tipo de carga y pedir cédula
    tipo_carga = None
    if "1" in texto or "lechon" in texto:
        tipo_carga = "Lechones"
    elif "2" in texto or "concentrado" in texto:
        tipo_carga = "Concentrado"
    elif "3" in texto or "cerdo" in texto or "gordo" in texto:
        tipo_carga = "Cerdos Gordos"
    elif "4" in texto or "combustible" in texto:
        tipo_carga = "Combustible"
    elif "5" in texto or "vac" in texto:
        # Opción 5: Peso Vacío - flujo diferente
        await state.update_data(tipo_carga="Peso Vacío", es_peso_vacio=True)
        
        keyboard = ReplyKeyboardBuilder()
        keyboard.button(text="1. Lechones")
        keyboard.button(text="2. Concentrado")
        keyboard.button(text="3. Cerdos Gordos")
        keyboard.button(text="4. Combustible")
        keyboard.adjust(2, 2)
        
        await message.answer(
            "🚛 *PESO VACÍO*\n\n"
            "¿Qué entregó o qué va a cargar?\n\n"
            "1️⃣ Lechones\n"
            "2️⃣ Concentrado\n"
            "3️⃣ Cerdos Gordos\n"
            "4️⃣ Combustible\n\n"
            "Seleccione una opción:",
            reply_markup=keyboard.as_markup(resize_keyboard=True),
            parse_mode="Markdown"
        )
        await state.set_state(ConductoresState.peso_vacio_tipo_carga)
        return
    else:
        await message.answer("⚠️ Opción no válida. Por favor seleccione una de las opciones del menú.")
        return
    
    # Para opciones 1-4: guardar tipo y pedir cédula
    await state.update_data(tipo_carga=tipo_carga, es_peso_vacio=False)
    await message.answer(
        f"✅ Tipo de carga: *{tipo_carga}*\n\n"
        f"Por favor, ingrese su *cédula*:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(ConductoresState.cedula)

# Handler para Peso Vacío - selección de tipo de carga
@dp.message(ConductoresState.peso_vacio_tipo_carga)
async def procesar_peso_vacio_tipo_carga(message: types.Message, state: FSMContext):
    """Procesa qué entregó o va a cargar en peso vacío"""
    texto = message.text.strip().lower()
    
    tipo_carga_referencia = None
    if "1" in texto or "lechon" in texto:
        tipo_carga_referencia = "Lechones"
    elif "2" in texto or "concentrado" in texto:
        tipo_carga_referencia = "Concentrado"
    elif "3" in texto or "cerdo" in texto or "gordo" in texto:
        tipo_carga_referencia = "Cerdos Gordos"
    elif "4" in texto or "combustible" in texto:
        tipo_carga_referencia = "Combustible"
    else:
        await message.answer("⚠️ Opción no válida. Por favor seleccione una de las opciones.")
        return
    
    await state.update_data(tipo_carga_referencia=tipo_carga_referencia)
    await message.answer(
        f"✅ Referencia: *{tipo_carga_referencia}*\n\n"
        f"Por favor, ingrese su *cédula*:",
        reply_markup=ReplyKeyboardRemove(),
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

    # Mensaje específico para báscula (es un botón, no texto escrito)
    if campo.lower() == "báscula":
        pregunta = "¿Está seguro que es la ubicación que quiere ingresar?"
    else:
        pregunta = "¿Está seguro que está correctamente escrito?"

    await message.answer(
        f"Usted ingresó: *{valor}*\n\n"
        f"{pregunta}\n\n"
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

    if not validar_cedula_sitio3(cedula):
        await message.answer(
            "⚠️ Cédula inválida.\n\n"
            "Debe contener solo números y tener entre 6 y 12 dígitos.\n\n"
            "Por favor, intente nuevamente:"
        )
        return

    await state.update_data(cedula=cedula)
    await message.answer(
        f"📋 Cédula ingresada: *{cedula}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(ConductoresState.confirmar_cedula)

@dp.message(ConductoresState.confirmar_cedula, F.text == "1")
async def confirmar_cedula_conductor_si(message: types.Message, state: FSMContext):
    """Confirma la cédula y verifica múltiples cédulas"""
    data = await state.get_data()
    cedula = data.get('cedula')
    telegram_user_id = message.from_user.id

    # Verificar si hay múltiples cédulas (alerta de seguridad)
    hay_alerta, cedulas_previas = await verificar_multiples_cedulas(telegram_user_id, cedula)

    if hay_alerta:
        username = message.from_user.username or message.from_user.full_name or "Desconocido"
        await enviar_alerta_seguridad(
            telegram_user_id=telegram_user_id,
            username=username,
            cedula_actual=cedula,
            cedulas_previas=cedulas_previas,
            tipo_operacion="Conductores"
        )

    await message.answer(
        f"✅ Cédula: *{cedula}*\n\n"
        f"Ahora, ingrese la *placa del camión*:\n"
        f"_(Formato: 3 letras + 3 números, ejemplo: NHU982)_",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(ConductoresState.placa)

@dp.message(ConductoresState.confirmar_cedula, F.text == "2")
async def confirmar_cedula_conductor_no(message: types.Message, state: FSMContext):
    """Permite editar la cédula"""
    await message.answer(
        "Por favor, ingrese nuevamente su *cédula*:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(ConductoresState.cedula)

@dp.message(ConductoresState.confirmar_cedula)
async def confirmar_cedula_conductor_invalido(message: types.Message, state: FSMContext):
    """Maneja respuesta inválida en confirmación"""
    await message.answer(
        "⚠️ Opción no válida.\n\n"
        "Por favor escriba:\n"
        "1️⃣ para confirmar\n"
        "2️⃣ para editar"
    )

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
        tipo_carga = data.get("tipo_carga")
        es_peso_vacio = data.get("es_peso_vacio", False)
        await state.update_data(placa=placa)
        
        # Si es peso vacío, ir directamente a báscula
        if es_peso_vacio:
            tipo_ref = data.get("tipo_carga_referencia", "")
            keyboard = ReplyKeyboardBuilder()
            keyboard.button(text="1. Granja")
            keyboard.button(text="2. Bogotá")
            keyboard.adjust(2)
            
            await message.answer(
                f"✅ Placa: *{placa}*\n\n"
                f"📍 *SELECCIÓN DE BÁSCULA*\n\n"
                f"¿En qué báscula se realizará el pesaje?\n\n"
                f"1️⃣ Granja\n"
                f"2️⃣ Bogotá\n\n"
                f"Seleccione una opción:",
                reply_markup=keyboard.as_markup(resize_keyboard=True),
                parse_mode="Markdown"
            )
            await state.set_state(ConductoresState.bascula)
            return
        
        # Si NO es peso vacío, continuar con el flujo según tipo de carga
        if tipo_carga == "Lechones" or tipo_carga == "Cerdos Gordos":
            animal_tipo = "lechones" if tipo_carga == "Lechones" else "cerdos gordos"
            await message.answer(
                f"✅ Placa: *{placa}*\n\n"
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
                f"✅ Placa: *{placa}*\n\n"
                f"¿Qué tipo de combustible?\n\n"
                f"Seleccione una opción:",
                reply_markup=keyboard.as_markup(resize_keyboard=True),
                parse_mode="Markdown"
            )
            await state.set_state(ConductoresState.tipo_combustible)
        
        elif tipo_carga == "Concentrado":
            await message.answer(
                f"✅ Placa: *{placa}*\n\n"
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
        "Su pesaje ha sido registrado correctamente.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )

    await finalizar_flujo(message, state)

async def enviar_notificacion_grupo_conductor(data: dict):
    """Envía notificación al grupo de Telegram con la información del conductor"""
    if not GROUP_CHAT_ID:
        print("⚠️ GROUP_CHAT_ID no configurado. No se enviará notificación.")
        return

    try:
        tipo_carga = data.get('tipo_carga')
        bascula = data.get('bascula')

        # Construir hashtags dinámicos
        hashtags = ["#Conductores"]

        # Hashtag por tipo de carga
        if tipo_carga == "Lechones":
            hashtags.append("#Lechones")
        elif tipo_carga == "Cerdos Gordos":
            hashtags.append("#CerdosGordos")
        elif tipo_carga == "Concentrado":
            hashtags.append("#Concentrado")
        elif tipo_carga == "Combustible":
            hashtags.append("#Combustible")

        # Hashtag por báscula
        if bascula == "Báscula Italcol":
            hashtags.append("#Italcol")
        elif bascula == "Bogotá":
            hashtags.append("#Bogota")
        elif bascula == "Finca Tranquera":
            hashtags.append("#FincaTranquera")

        # Hashtag de alerta si hay cerdos muertos
        cerdos_muertos = data.get('cerdos_muertos', 0)
        if cerdos_muertos > 0:
            hashtags.append("#AlertaCritica")
            hashtags.append("#CerdosMuertos")

        # Crear mensaje
        mensaje_lineas = ["🚛 *NUEVO REGISTRO DE CONDUCTOR*"]
        mensaje_lineas.append(" ".join(hashtags) + "\n")

        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")
        mensaje_lineas.append(f"📅 Fecha: {timestamp}\n")

        mensaje_lineas.append(f"👤 Cédula: *{data.get('cedula')}*")
        mensaje_lineas.append(f"🚛 Placa: *{data.get('placa')}*")
        mensaje_lineas.append(f"📦 Tipo de carga: *{tipo_carga}*\n")

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

        mensaje = "\n".join(mensaje_lineas)

        # Enviar mensaje de texto
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=mensaje,
            parse_mode="Markdown"
        )

        # Enviar foto de FACTURA como archivo adjunto (si existe)
        if tipo_carga == "Concentrado" and data.get('factura_foto'):
            factura_path = data.get('factura_foto')
            # Si es un path local (no URL de Drive)
            if factura_path and not factura_path.startswith('http') and os.path.exists(factura_path):
                try:
                    with open(factura_path, 'rb') as photo:
                        await bot.send_photo(
                            chat_id=GROUP_CHAT_ID,
                            photo=types.BufferedInputFile(photo.read(), filename="factura.jpg"),
                            caption=f"📸 Foto de Factura - {data.get('numero_factura')}"
                        )
                    print("✅ Foto de factura enviada al grupo")
                except Exception as e_factura:
                    print(f"⚠️ Error enviando foto de factura: {e_factura}")

        # Enviar foto de PESAJE como archivo adjunto (si existe)
        if data.get('foto_pesaje'):
            pesaje_path = data.get('foto_pesaje')
            # Si es un path local (no URL de Drive)
            if pesaje_path and not pesaje_path.startswith('http') and os.path.exists(pesaje_path):
                try:
                    with open(pesaje_path, 'rb') as photo:
                        await bot.send_photo(
                            chat_id=GROUP_CHAT_ID,
                            photo=types.BufferedInputFile(photo.read(), filename="pesaje.jpg"),
                            caption=f"📸 Foto de Pesaje - {data.get('placa')} - {data.get('peso'):,.2f} kg"
                        )
                    print("✅ Foto de pesaje enviada al grupo")
                except Exception as e_pesaje:
                    print(f"⚠️ Error enviando foto de pesaje: {e_pesaje}")

        print("✅ Notificación completa enviada al grupo")

    except Exception as e:
        print(f"⚠️ Error enviando notificación al grupo: {e}")

# ==================== OPERARIO SITIO 1 - REGISTRO DE LECHONES ==================== #

@dp.message(OperarioSitio1State.cedula)
async def procesar_cedula_sitio1(message: types.Message, state: FSMContext):
    """Procesa la cédula del operario"""
    cedula = message.text.strip()

    if not validar_cedula_sitio3(cedula):
        await message.answer(
            "⚠️ Cédula inválida.\n\n"
            "Debe contener solo números y tener entre 6 y 12 dígitos.\n\n"
            "Por favor, intente nuevamente:"
        )
        return

    await state.update_data(cedula=cedula)
    await message.answer(
        f"📋 Cédula ingresada: *{cedula}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(OperarioSitio1State.confirmar_cedula)

@dp.message(OperarioSitio1State.confirmar_cedula, F.text == "1")
async def confirmar_cedula_sitio1_si(message: types.Message, state: FSMContext):
    """Confirma la cédula y verifica múltiples cédulas"""
    data = await state.get_data()
    cedula = data.get('cedula')
    telegram_user_id = message.from_user.id

    # Verificar si hay múltiples cédulas (alerta de seguridad)
    hay_alerta, cedulas_previas = await verificar_multiples_cedulas(telegram_user_id, cedula)

    if hay_alerta:
        username = message.from_user.username or message.from_user.full_name or "Desconocido"
        await enviar_alerta_seguridad(
            telegram_user_id=telegram_user_id,
            username=username,
            cedula_actual=cedula,
            cedulas_previas=cedulas_previas,
            tipo_operacion="Operario Sitio 1"
        )

    await message.answer(
        f"✅ Cédula: *{cedula}*\n\n"
        f"¿Cuántos *lechones* va a pesar?\n"
        f"_(Ingrese un número)_",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(OperarioSitio1State.cantidad_lechones)

@dp.message(OperarioSitio1State.cantidad_lechones)
async def procesar_cantidad_lechones(message: types.Message, state: FSMContext):
    """Procesa la cantidad de lechones a pesar"""
    es_valido, cantidad, error = validar_numero_entero(message.text.strip(), minimo=1, maximo=10000)
    
    if not es_valido:
        await message.answer(f"⚠️ {error}\n\nIntente nuevamente:")
        return
    
    await state.update_data(cantidad_lechones=cantidad)
    
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text="1. Sí, confirmar")
    keyboard.button(text="2. No, editar")
    keyboard.adjust(2)
    
    await message.answer(
        f"Cantidad de lechones: *{cantidad}*\n\n"
        f"¿Es correcta?\n\n"
        f"1️⃣ Sí, confirmar\n"
        f"2️⃣ No, editar\n\n"
        f"Escriba el número de la opción:",
        reply_markup=keyboard.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )
    await state.set_state(OperarioSitio1State.confirmar_cantidad_lechones)

@dp.message(OperarioSitio1State.confirmar_cantidad_lechones)
async def confirmar_cantidad_lechones(message: types.Message, state: FSMContext):
    """Confirma la cantidad de lechones o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "editar" in texto or "no" in texto:
        await message.answer(
            "¿Cuántos *lechones* va a pesar?\n"
            "_(Ingrese un número)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.cantidad_lechones)
        return
    
    if "1" in texto or "confirmar" in texto or "sí" in texto or "si" in texto:
        data = await state.get_data()
        cantidad_lechones = data.get('cantidad_lechones')
        
        await message.answer(
            f"✅ Cantidad de lechones: *{cantidad_lechones}*\n\n"
            f"¿Cuántos *pesajes* va a registrar?\n"
            f"_(Ejemplo: 30 pesajes)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.cantidad_pesajes)
        return
    
    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Editar:")

@dp.message(OperarioSitio1State.confirmar_cedula, F.text == "2")
async def confirmar_cedula_sitio1_no(message: types.Message, state: FSMContext):
    """Permite editar la cédula"""
    await message.answer(
        "Por favor, ingrese nuevamente su *cédula*:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(OperarioSitio1State.cedula)

@dp.message(OperarioSitio1State.confirmar_cedula)
async def confirmar_cedula_sitio1(message: types.Message, state: FSMContext):
    """Confirma la cédula o permite modificarla"""
    texto = message.text.strip().lower()

    if "2" in texto or "modificar" in texto:
        await message.answer(
            "Por favor, ingrese nuevamente su *cédula*:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.cedula)
        return

    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        cedula = data.get("cedula_temp")
        await state.update_data(cedula=cedula)

        await message.answer(
            f"✅ Cédula: *{cedula}*\n\n"
            f"¿Cuántos *pesajes* va a registrar?\n"
            f"_(Ejemplo: 30 pesajes)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.cantidad_pesajes)
        return

    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

@dp.message(OperarioSitio1State.cantidad_pesajes)
async def procesar_cantidad_pesajes(message: types.Message, state: FSMContext):
    """Procesa la cantidad de pesajes"""
    es_valido, cantidad, error = validar_numero_entero(message.text.strip(), minimo=1, maximo=1000)
    
    if not es_valido:
        await message.answer(f"⚠️ {error}\n\nIntente nuevamente:")
        return
    
    await state.update_data(cantidad_pesajes_temp=cantidad)
    await preguntar_confirmacion(message, str(cantidad), "cantidad de pesajes")
    await state.set_state(OperarioSitio1State.confirmar_cantidad_pesajes)

@dp.message(OperarioSitio1State.confirmar_cantidad_pesajes)
async def confirmar_cantidad_pesajes(message: types.Message, state: FSMContext):
    """Confirma la cantidad de pesajes o permite modificarla"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "¿Cuántos *pesajes* va a registrar?\n"
            f"_(Ejemplo: 30 pesajes)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.cantidad_pesajes)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        cantidad = data.get("cantidad_pesajes_temp")
        await state.update_data(cantidad_pesajes=cantidad)
        
        await message.answer(
            f"✅ Cantidad de pesajes: *{cantidad}*\n\n"
            f"¿Cuántos *lechones* hay por cada pesaje?\n"
            f"_(Ejemplo: 10 lechones por pesaje)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.lechones_por_pesaje)
        return
    
    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

@dp.message(OperarioSitio1State.lechones_por_pesaje)
async def procesar_lechones_por_pesaje(message: types.Message, state: FSMContext):
    """Procesa la cantidad de lechones por pesaje"""
    es_valido, cantidad, error = validar_numero_entero(message.text.strip(), minimo=1, maximo=100)
    
    if not es_valido:
        await message.answer(f"⚠️ {error}\n\nIntente nuevamente:")
        return
    
    await state.update_data(lechones_por_pesaje_temp=cantidad)
    await preguntar_confirmacion(message, str(cantidad), "lechones por pesaje")
    await state.set_state(OperarioSitio1State.confirmar_lechones_por_pesaje)

@dp.message(OperarioSitio1State.confirmar_lechones_por_pesaje)
async def confirmar_lechones_por_pesaje(message: types.Message, state: FSMContext):
    """Confirma la cantidad de lechones por pesaje"""
    texto = message.text.strip().lower()
    
    if "2" in texto or "modificar" in texto:
        await message.answer(
            "¿Cuántos *lechones* hay por cada pesaje?",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.lechones_por_pesaje)
        return
    
    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        lechones = data.get("lechones_por_pesaje_temp")
        await state.update_data(
            lechones_por_pesaje=lechones,
            pesaje_actual=1,
            pesos=[],
            fotos=[]
        )
        
        await message.answer(
            f"✅ Lechones por pesaje: *{lechones}*\n\n"
            f"📊 Ingrese el *peso del pesaje #1* en kilogramos:\n"
            f"_(Este pesaje contiene {lechones} lechones)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.peso_pesaje)
        return
    
    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

@dp.message(OperarioSitio1State.peso_pesaje)
async def procesar_peso_pesaje(message: types.Message, state: FSMContext):
    """Procesa el peso de un pesaje"""
    es_valido, peso, error = validar_galones(message.text.strip())  # Reutilizamos validador de decimales

    if not es_valido or peso <= 0:
        await message.answer(
            f"⚠️ Peso inválido. Ingrese un número válido mayor a 0 kg\n\n"
            f"Intente nuevamente:"
        )
        return

    data = await state.get_data()
    pesaje_actual = data.get("pesaje_actual")
    lechones_por_pesaje = data.get("lechones_por_pesaje")

    await state.update_data(peso_temp=peso)
    await preguntar_confirmacion(message, f"{peso:,.2f} kg", f"peso del pesaje #{pesaje_actual} ({lechones_por_pesaje} lechones)")
    await state.set_state(OperarioSitio1State.confirmar_peso)

@dp.message(OperarioSitio1State.confirmar_peso)
async def confirmar_peso_pesaje(message: types.Message, state: FSMContext):
    """Confirma el peso del pesaje o permite modificarlo"""
    texto = message.text.strip().lower()

    if "2" in texto or "modificar" in texto:
        data = await state.get_data()
        pesaje_actual = data.get("pesaje_actual")
        lechones_por_pesaje = data.get("lechones_por_pesaje")
        await message.answer(
            f"📊 Ingrese nuevamente el *peso del pesaje #{pesaje_actual}* en kilogramos:\n"
            f"_(Este pesaje contiene {lechones_por_pesaje} lechones)_",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.set_state(OperarioSitio1State.peso_pesaje)
        return

    if "1" in texto or "confirmar" in texto:
        data = await state.get_data()
        peso = data.get("peso_temp")
        pesaje_actual = data.get("pesaje_actual")
        cantidad_pesajes = data.get("cantidad_pesajes")
        lechones_por_pesaje = data.get("lechones_por_pesaje")
        pesos = data.get("pesos", [])
        
        # Guardar peso en la lista
        pesos.append(peso)
        await state.update_data(pesos=pesos)
        
        # Verificar si hay más pesajes
        if pesaje_actual < cantidad_pesajes:
            siguiente = pesaje_actual + 1
            await state.update_data(pesaje_actual=siguiente)
            
            await message.answer(
                f"✅ Pesaje #{pesaje_actual} registrado: *{peso:,.2f} kg*\n\n"
                f"📊 Ingrese el *peso del pesaje #{siguiente}* en kilogramos:\n"
                f"_(Progreso: {pesaje_actual}/{cantidad_pesajes} | {lechones_por_pesaje} lechones por pesaje)_",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            await state.set_state(OperarioSitio1State.peso_pesaje)
        else:
            # Todos los pesajes completados, pedir foto final
            await message.answer(
                f"✅ Pesaje #{pesaje_actual} registrado: *{peso:,.2f} kg*\n\n"
                f"🎉 *¡Todos los {cantidad_pesajes} pesajes han sido registrados!*\n\n"
                f"📸 Ahora envíe una *foto de confirmación* del proceso:",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            await state.set_state(OperarioSitio1State.foto_final)
        return
    
    await message.answer("⚠️ Opción no válida. Seleccione 1 para Confirmar o 2 para Modificar:")

@dp.message(OperarioSitio1State.foto_final, F.photo)
async def procesar_foto_final(message: types.Message, state: FSMContext):
    """Procesa la foto final de confirmación y finaliza el registro"""
    data = await state.get_data()
    
    # Descargar foto
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_path = f"temp_foto_final_{message.from_user.id}.jpg"
    await bot.download_file(file.file_path, file_path)

    # Subir a Google Drive
    foto_url = upload_to_drive(file_path, f"foto_confirmacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    
    # Eliminar archivo temporal
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # Guardar URL de foto
    await state.update_data(foto_confirmacion=foto_url if foto_url else "Sin foto")
    
    # Finalizar registro
    await finalizar_registro_sitio1(message, state)

@dp.message(OperarioSitio1State.foto_final)
async def foto_final_invalida(message: types.Message, state: FSMContext):
    """Handler para cuando no se envía una foto"""
    await message.answer(
        f"⚠️ Por favor envíe una *foto* de confirmación del proceso.\n\n"
        f"_(No se aceptan archivos de texto)_",
        parse_mode="Markdown"
    )

async def finalizar_registro_sitio1(message: types.Message, state: FSMContext):
    """Finaliza el registro y envía resumen"""
    data = await state.get_data()

    cedula = data.get("cedula")
    telegram_id = data.get("telegram_id")
    cantidad_pesajes = data.get("cantidad_pesajes")
    lechones_por_pesaje = data.get("lechones_por_pesaje")
    pesos = data.get("pesos", [])
    foto_confirmacion = data.get("foto_confirmacion", "Sin foto")
    
    # Calcular estadísticas
    peso_total = sum(pesos)
    total_lechones = cantidad_pesajes * lechones_por_pesaje
    peso_promedio_por_lechon = peso_total / total_lechones if total_lechones > 0 else 0
    peso_promedio_por_pesaje = peso_total / len(pesos) if pesos else 0
    
    # Guardar en base de datos
    await guardar_registro_sitio1(data, peso_total, peso_promedio_por_lechon, peso_promedio_por_pesaje, total_lechones)
    
    # Enviar notificación al grupo
    await enviar_notificacion_grupo_sitio1(data, peso_total, peso_promedio_por_lechon, peso_promedio_por_pesaje, total_lechones)
    
    # Crear resumen para el usuario
    resumen = f"✅ *REGISTRO COMPLETADO*\n\n"
    resumen += f"👤 Cédula: *{cedula}*\n"
    resumen += f"📊 Pesajes registrados: *{cantidad_pesajes}*\n"
    resumen += f"🐷 Lechones por pesaje: *{lechones_por_pesaje}*\n"
    resumen += f"🐷 Total de lechones: *{total_lechones}*\n"
    resumen += f"⚖️ Peso total: *{peso_total:,.2f} kg*\n"
    resumen += f"📈 Peso promedio por lechón: *{peso_promedio_por_lechon:,.2f} kg*\n"
    resumen += f"📈 Peso promedio por pesaje: *{peso_promedio_por_pesaje:,.2f} kg*\n\n"
    resumen += f"*DETALLE POR PESAJE:*\n\n"
    
    for i, peso in enumerate(pesos, 1):
        peso_por_lechon = peso / lechones_por_pesaje
        resumen += f"Pesaje #{i}: {peso:,.2f} kg ({peso_por_lechon:,.2f} kg/lechón)\n"
    
    await message.answer(resumen, parse_mode="Markdown")
    await finalizar_flujo(message, state)

async def guardar_registro_sitio1(data: dict, peso_total: float, peso_promedio_lechon: float, peso_promedio_pesaje: float, total_lechones: int):
    """Guarda el registro en la base de datos"""
    conn = None
    try:
        conn = await get_db_connection()
        if not conn:
            print("⚠️ No se pudo conectar a la base de datos")
            return
        
        # Crear tabla si no existe
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS operario_fijo_granja (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                cedula VARCHAR(20) NOT NULL,
                cantidad_pesajes INTEGER NOT NULL,
                lechones_por_pesaje INTEGER NOT NULL,
                total_lechones INTEGER NOT NULL,
                peso_total DECIMAL(10, 2) NOT NULL,
                peso_promedio_por_lechon DECIMAL(10, 2) NOT NULL,
                peso_promedio_por_pesaje DECIMAL(10, 2) NOT NULL,
                pesos_detalle TEXT,
                foto_confirmacion TEXT,
                fecha TIMESTAMP DEFAULT NOW()
            )
        ''')
        
        pesos = data.get("pesos", [])
        foto_confirmacion = data.get("foto_confirmacion", "Sin foto")
        
        # Convertir lista de pesos a string JSON
        import json
        pesos_json = json.dumps(pesos)
        
        # Insertar registro
        await conn.execute('''
            INSERT INTO operario_fijo_granja (
                telegram_id, cedula, cantidad_pesajes, lechones_por_pesaje, total_lechones,
                peso_total, peso_promedio_por_lechon, peso_promedio_por_pesaje,
                pesos_detalle, foto_confirmacion
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ''',
            data.get('telegram_id'),
            data.get('cedula'),
            data.get('cantidad_pesajes'),
            data.get('lechones_por_pesaje'),
            total_lechones,
            peso_total,
            peso_promedio_lechon,
            peso_promedio_pesaje,
            pesos_json,
            foto_confirmacion
        )
        
        print("✅ Registro de Sitio 1 guardado en base de datos")
    except Exception as e:
        print(f"⚠️ Error guardando en base de datos: {e}")
    finally:
        if conn:
            await release_db_connection(conn)

async def enviar_notificacion_grupo_sitio1(data: dict, peso_total: float, peso_promedio_lechon: float, peso_promedio_pesaje: float, total_lechones: int):
    """Envía notificación al grupo de Telegram"""
    if not GROUP_CHAT_ID:
        print("⚠️ GROUP_CHAT_ID no configurado. No se enviará notificación.")
        return

    try:
        pesos = data.get("pesos", [])
        foto_confirmacion = data.get("foto_confirmacion", "Sin foto")
        cantidad_pesajes = data.get('cantidad_pesajes')
        lechones_por_pesaje = data.get('lechones_por_pesaje')
        
        # Crear mensaje
        mensaje = "🐷 *NUEVO REGISTRO - OPERARIO SITIO 1*\n"
        mensaje += "#Sitio1 #Lechones\n\n"

        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")
        mensaje += f"📅 Fecha: {timestamp}\n\n"

        mensaje += f"👤 Cédula: *{data.get('cedula')}*\n"
        mensaje += f"📦 Pesajes registrados: *{cantidad_pesajes}*\n"
        mensaje += f"🐷 Lechones por pesaje: *{lechones_por_pesaje}*\n"
        mensaje += f"🐷 Total de lechones: *{total_lechones}*\n"
        mensaje += f"⚖️ Peso total: *{peso_total:,.2f} kg*\n"
        mensaje += f"📊 Peso promedio por lechón: *{peso_promedio_lechon:,.2f} kg*\n"
        mensaje += f"📊 Peso promedio por pesaje: *{peso_promedio_pesaje:,.2f} kg*\n\n"
        
        mensaje += "*DETALLE POR PESAJE:*\n"
        for i, peso in enumerate(pesos, 1):
            peso_por_lechon = peso / lechones_por_pesaje
            mensaje += f"Pesaje #{i}: {peso:,.2f} kg ({peso_por_lechon:,.2f} kg/lechón)\n"
        
        if foto_confirmacion and foto_confirmacion != "Sin foto":
            mensaje += f"\n📸 [Ver foto de confirmación]({foto_confirmacion})"
        
        # Enviar mensaje
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=mensaje,
            parse_mode="Markdown"
        )

        # Enviar TODAS las fotos como archivos adjuntos
        if fotos_locales:
            for i, foto_path in enumerate(fotos_locales, 1):
                if foto_path and os.path.exists(foto_path):
                    try:
                        # Calcular cuántos lechones hay en este grupo
                        lechones_pesados = (i - 1) * lechones_por_grupo
                        lechones_en_este_grupo = min(lechones_por_grupo, cantidad_lechones - lechones_pesados)

                        with open(foto_path, 'rb') as photo:
                            await bot.send_photo(
                                chat_id=GROUP_CHAT_ID,
                                photo=types.BufferedInputFile(photo.read(), filename=f"pesaje_{i}.jpg"),
                                caption=f"📸 Pesaje #{i} - {pesos[i-1]:,.2f} kg ({lechones_en_este_grupo} lechones)"
                            )
                        print(f"✅ Foto del pesaje #{i} enviada al grupo")
                    except Exception as e_foto:
                        print(f"⚠️ Error enviando foto del pesaje #{i}: {e_foto}")

        print("✅ Notificación completa de Sitio 1 enviada al grupo")

    except Exception as e:
        print(f"⚠️ Error enviando notificación al grupo: {e}")

# ==================== OPERARIO SITIO 3 - SUBMENÚ ==================== #
@dp.message(RegistroState.sitio3_menu, F.text == "1")
async def sitio3_registro_consumo_lote(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 1: Registro de consumo por lote"""
    # Inicializar datos de sesión
    session_id = str(uuid.uuid4())
    await state.update_data(
        sitio3_session_id=session_id,
        sitio3_corrales=[]  # Lista para acumular corrales
    )
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.sitio3_cedula)

@dp.message(RegistroState.sitio3_menu, F.text == "2")
async def sitio3_ingreso_concentrado_silo(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 2: Ingreso concentrado en silo"""
    # Inicializar datos de sesión
    session_id = str(uuid.uuid4())
    await state.update_data(
        medicion_session_id=session_id,
        medicion_silos_procesados=[]  # Lista de silos ya procesados
    )
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.medicion_cedula)

@dp.message(RegistroState.sitio3_menu, F.text == "3")
async def sitio3_ingreso_lechones(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 3: Ingreso de lechones"""
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.descarga_cedula)

@dp.message(RegistroState.sitio3_menu, F.text == "4")
async def sitio3_registro_celdas_carga(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 4: Registro celdas de carga"""
    session_id = str(uuid.uuid4())
    await state.update_data(
        celdas_session_id=session_id,
        celdas_registros=[]  # Lista de registros de celdas
    )
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.celdas_cedula)

@dp.message(RegistroState.sitio3_menu, F.text == "5")
async def sitio3_registro_combustible(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 5: Registro de combustible"""
    session_id = str(uuid.uuid4())
    await state.update_data(combustible_session_id=session_id)
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.combustible_cedula)

@dp.message(RegistroState.sitio3_menu, F.text == "6")
async def sitio3_traslado_corrales(message: types.Message, state: FSMContext):
    """Sitio 3 - Opción 6: Traslado entre corrales"""
    session_id = str(uuid.uuid4())
    await state.update_data(traslado_session_id=session_id)
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.traslado_cedula)

# ==================== OPERARIO SITIO 3 - REGISTRO DE CONSUMO POR LOTE ==================== #

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
    """Confirma cédula y pasa a número de banda"""
    # Verificar si hay múltiples cédulas (alerta de seguridad)
    data = await state.get_data()
    cedula = data.get('sitio3_cedula')
    telegram_user_id = message.from_user.id

    hay_alerta, cedulas_previas = await verificar_multiples_cedulas(telegram_user_id, cedula)

    if hay_alerta:
        username = message.from_user.username
        if username:
            username = f"@{username}"
        else:
            first_name = message.from_user.first_name or ""
            last_name = message.from_user.last_name or ""
            username = f"{first_name} {last_name}".strip() or "Sin nombre"

        await enviar_alerta_seguridad(
            telegram_user_id=telegram_user_id,
            username=username,
            cedula_actual=cedula,
            cedulas_previas=cedulas_previas,
            tipo_operacion="Registro de consumo por lote"
        )

    await message.answer(
        "🏷️ Escriba el número de banda\n\n"
        "_(ejemplo: 212-b1)_",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_numero_banda)

@dp.message(RegistroState.sitio3_confirmar_cedula, F.text == "2")
async def sitio3_confirmar_cedula_no(message: types.Message, state: FSMContext):
    """Rechaza cédula y vuelve a preguntar"""
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.sitio3_cedula)

@dp.message(RegistroState.sitio3_confirmar_cedula)
async def sitio3_confirmar_cedula_invalido(message: types.Message, state: FSMContext):
    """Handler para respuestas inválidas en confirmación de cédula"""
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 2: Número de Banda (sin confirmación intermedia)
@dp.message(RegistroState.sitio3_numero_banda)
async def sitio3_get_banda(message: types.Message, state: FSMContext):
    """Captura y valida número de banda, pasa directo a corrales"""
    banda_texto = message.text.strip()

    es_valido, banda, mensaje_error = validar_numero_banda(banda_texto)

    if not es_valido:
        await message.answer(
            f"⚠️ {mensaje_error}\n\n"
            "Por favor, intente nuevamente:\n"
            "_(ejemplo: 212-b1)_",
            parse_mode="Markdown"
        )
        return

    # Guardar banda y pasar directo a corrales
    await state.update_data(sitio3_banda_temp=banda)

    await message.answer(
        "📍 ¿En qué corrales van a ubicarse los lechones?\n\n"
        "Por favor ingrese el rango en formato: *#-#*\n\n"
        "⚠️ _Máximo 9 corrales por registro_\n\n"
        "*Ejemplos válidos:*\n"
        "• `1-9`\n"
        "• `10-18`\n"
        "• `5-10`",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.sitio3_rango_corrales)

# PASO 3: Rango de Corrales (sin confirmación intermedia)
@dp.message(RegistroState.sitio3_rango_corrales)
async def sitio3_get_rango(message: types.Message, state: FSMContext):
    """Captura y valida rango de corrales con máximo de 9"""
    rango = message.text.strip()

    # Validar con máximo de 9 corrales
    es_valido, mensaje_error = validar_rango_corrales(rango, max_rango=9)

    if not es_valido:
        await message.answer(
            f"⚠️ {mensaje_error}\n\n"
            "Por favor ingrese el rango en formato: *#-#*\n"
            "⚠️ _Máximo 9 corrales. No puede empezar en 0._\n"
            "Ejemplo: `1-9`",
            parse_mode="Markdown"
        )
        return

    # Guardar rango y pasar a tipo de comida
    await state.update_data(sitio3_rango_temp=rango)

    builder = ReplyKeyboardBuilder()
    builder.button(text="Levante")
    builder.button(text="Engorde / Levante 3 medicado")
    builder.button(text="Finalizador")
    builder.adjust(1)  # 1 botón por fila para mejor legibilidad

    await message.answer(
        "🍽️ ¿Qué tipo de comida están consumiendo estos animales?",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.sitio3_tipo_comida)

# PASO 4: Tipo de Comida - Muestra resumen final con confirmación
@dp.message(RegistroState.sitio3_tipo_comida, F.text.in_(["Levante", "Engorde / Levante 3 medicado", "Finalizador"]))
async def sitio3_get_tipo_comida(message: types.Message, state: FSMContext):
    """Captura tipo de comida y muestra resumen para confirmación"""
    tipo_comida = message.text
    await state.update_data(sitio3_tipo_comida_temp=tipo_comida)

    # Obtener datos para mostrar resumen
    data = await state.get_data()
    banda = data.get('sitio3_banda_temp')
    rango = data.get('sitio3_rango_temp')

    # Mostrar resumen de la operación con opciones de edición
    resumen = (
        "📋 *RESUMEN DE LA OPERACIÓN*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"1️⃣ Corrales: *{rango}*\n"
        f"2️⃣ Banda: *{banda}*\n"
        f"3️⃣ Tipo de comida: *{tipo_comida}*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Si desea cambiar alguna opción,\n"
        "escriba el número (1, 2 o 3).\n\n"
        "✅ Si todo está correcto, escriba *OK*"
    )

    await message.answer(
        resumen,
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.sitio3_resumen_confirmacion)

@dp.message(RegistroState.sitio3_tipo_comida)
async def sitio3_tipo_comida_invalido(message: types.Message, state: FSMContext):
    """Handler para opciones inválidas"""
    await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

# PASO 5: Confirmación final o edición
@dp.message(RegistroState.sitio3_resumen_confirmacion)
async def sitio3_resumen_confirmacion(message: types.Message, state: FSMContext):
    """Procesa la confirmación o edición del resumen"""
    respuesta = message.text.strip().upper()

    if respuesta == "OK":
        # Confirmar y guardar el corral
        data = await state.get_data()

        # Agregar este corral a la lista de corrales
        corrales = data.get('sitio3_corrales', [])
        corrales.append({
            'banda': data['sitio3_banda_temp'],
            'rango': data['sitio3_rango_temp'],
            'tipo_comida': data['sitio3_tipo_comida_temp']
        })

        await state.update_data(sitio3_corrales=corrales)

        # Mostrar resumen y preguntar si desea agregar más
        resumen = "✅ Registro guardado correctamente.\n\n"
        resumen += "📊 *Total de bandas registradas:*\n"
        for i, corral in enumerate(corrales, 1):
            resumen += f"\n🔹 *Banda {i}:* {corral['banda']}\n"
            resumen += f"   • Corrales: {corral['rango']}\n"
            resumen += f"   • Comida: {corral['tipo_comida']}\n"

        resumen += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        resumen += f"📝 *Total de bandas registradas: {len(corrales)}*\n"

        await message.answer(resumen, parse_mode="Markdown")

        # Preguntar si desea registrar otra banda
        builder = ReplyKeyboardBuilder()
        builder.button(text="✅ Sí, otra banda")
        builder.button(text="❌ No, terminar")
        builder.adjust(2)

        await message.answer(
            "¿Desea registrar otra banda?",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        await state.set_state(RegistroState.sitio3_agregar_mas)

    elif respuesta == "1":
        # Editar corrales
        await message.answer(
            "📍 ¿En qué corrales van a ubicarse los lechones?\n\n"
            "Por favor ingrese el rango en formato: *#-#*\n\n"
            "⚠️ _Máximo 9 corrales por registro_\n\n"
            "Ejemplo: `1-9`",
            parse_mode="Markdown"
        )
        await state.set_state(RegistroState.sitio3_rango_corrales)

    elif respuesta == "2":
        # Editar banda
        await message.answer(
            "🏷️ Escriba el número de banda\n\n"
            "_(ejemplo: 212-b1)_",
            parse_mode="Markdown"
        )
        await state.set_state(RegistroState.sitio3_numero_banda)

    elif respuesta == "3":
        # Editar tipo de comida
        builder = ReplyKeyboardBuilder()
        builder.button(text="Levante")
        builder.button(text="Engorde / Levante 3 medicado")
        builder.button(text="Finalizador")
        builder.adjust(1)

        await message.answer(
            "🍽️ ¿Qué tipo de comida están consumiendo estos animales?",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        await state.set_state(RegistroState.sitio3_tipo_comida)

    else:
        await message.answer(
            "⚠️ Opción no válida.\n\n"
            "Escriba *1*, *2* o *3* para editar,\n"
            "o *OK* para confirmar.",
            parse_mode="Markdown"
        )

# PASO 6: Agregar Más Bandas o Terminar
@dp.message(RegistroState.sitio3_agregar_mas, F.text.in_(["✅ Sí, otra banda", "Sí", "Si", "1"]))
async def sitio3_agregar_otro_corral(message: types.Message, state: FSMContext):
    """Usuario quiere agregar otra banda"""
    await message.answer(
        "🏷️ Escriba el número de banda\n\n"
        "_(ejemplo: 212-b1)_",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.sitio3_numero_banda)

@dp.message(RegistroState.sitio3_agregar_mas, F.text.in_(["❌ No, terminar", "No", "2"]))
async def sitio3_terminar_registro(message: types.Message, state: FSMContext):
    """Usuario termina el registro - Guardar en BD y notificar"""
    await message.answer("⏳ Guardando registros...", reply_markup=types.ReplyKeyboardRemove())

    data = await state.get_data()
    cedula = data.get('sitio3_cedula')
    corrales = data.get('sitio3_corrales', [])
    session_id = data.get('sitio3_session_id')

    if not corrales:
        await message.answer("⚠️ No hay bandas registradas para guardar.")
        await volver_menu_sitio3(message, state)
        return

    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            fecha_registro = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Insertar cada banda como una fila separada
            telegram_user_id = message.from_user.id
            for corral in corrales:
                await conn.execute('''
                    INSERT INTO operario_sitio3_animales
                    (cedula_operario, bandas, rango_corrales, tipo_comida, fecha_registro, session_id, telegram_user_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''', cedula, corral['banda'], corral['rango'], corral['tipo_comida'], fecha_registro, session_id, telegram_user_id)

            print(f"✅ {len(corrales)} bandas guardadas en BD (session: {session_id})")
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
    total_bandas = len(corrales)

    # Generar notificación para el grupo de Telegram
    if GROUP_CHAT_ID:
        try:
            fecha_formateada = datetime.now().strftime('%d/%m/%Y %H:%M')

            mensaje_grupo = (
                "🐷 *NUEVO REGISTRO DE CONSUMO POR LOTE - SITIO 3*\n"
                "#Sitio3 #ConsumoLote\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Operario: `{cedula}`\n"
                f"🕒 Fecha: {fecha_formateada}\n\n"
                "📊 *BANDAS REGISTRADAS:*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )

            for i, corral in enumerate(corrales, 1):
                mensaje_grupo += (
                    f"🔹 *Banda {i}:* {corral['banda']}\n"
                    f"   • Corrales: {corral['rango']}\n"
                    f"   • Comida: {corral['tipo_comida']}\n\n"
                )

            mensaje_grupo += (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 *Total de bandas registradas: {total_bandas}*"
            )

            await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")
            print("✅ Notificación enviada al grupo")

        except Exception as e:
            print(f"⚠️ Error al enviar notificación al grupo: {e}")

    # Mostrar resumen al usuario
    resumen_usuario = (
        "✅ *Registro completado exitosamente*\n\n"
        "📊 *Resumen:*\n\n"
        f"• Total de bandas registradas: {total_bandas}\n\n"
        "Gracias por registrar la información."
    )

    await message.answer(resumen_usuario, parse_mode="Markdown")

    # Finalizar flujo
    await asyncio.sleep(1)
    await finalizar_flujo(message, state)

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
    # Verificar si hay múltiples cédulas (alerta de seguridad)
    data = await state.get_data()
    cedula = data.get('descarga_cedula')
    telegram_user_id = message.from_user.id

    hay_alerta, cedulas_previas = await verificar_multiples_cedulas(telegram_user_id, cedula)

    if hay_alerta:
        # Obtener nombre de usuario para la alerta
        username = message.from_user.username
        if username:
            username = f"@{username}"
        else:
            first_name = message.from_user.first_name or ""
            last_name = message.from_user.last_name or ""
            username = f"{first_name} {last_name}".strip() or "Sin nombre"

        # Enviar alerta al grupo
        await enviar_alerta_seguridad(
            telegram_user_id=telegram_user_id,
            username=username,
            cedula_actual=cedula,
            cedulas_previas=cedulas_previas,
            tipo_operacion="Descarga de Animales"
        )

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

    # Finalizar flujo
    await asyncio.sleep(1)
    await finalizar_flujo(message, state)

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

# ==================== FIN INGRESO DE LECHONES ==================== #

# ==================== OPERARIO SITIO 3 - INGRESO CONCENTRADO EN SILO ==================== #

# Validación de peso en kilos para descargue (máximo 25000 kg)
def validar_peso_kilos_descargue(valor: str) -> tuple[bool, float, str]:
    """
    Valida peso de descargue en kilos: decimal positivo, máximo 25000 kg
    Retorna: (es_valido, peso, mensaje_error)
    """
    valor_normalizado = valor.replace(",", ".")
    try:
        peso = float(valor_normalizado)
        if peso <= 0:
            return False, 0.0, "El peso debe ser mayor a 0"
        if peso > 25000:
            return False, 0.0, "El peso no puede superar 25,000 kilos"
        peso = round(peso, 2)
        return True, peso, ""
    except ValueError:
        return False, 0.0, "Debe ingresar un número válido (use punto o coma para decimales)"

# Validación de silo único (1-6)
def validar_silo_unico(valor: str) -> tuple[bool, int, str]:
    """
    Valida que se ingrese un solo número de silo (1-6)
    Retorna: (es_valido, numero_silo, mensaje_error)
    """
    valor_limpio = valor.strip()
    try:
        silo = int(valor_limpio)
        if silo < 1 or silo > 6:
            return False, 0, "El número de silo debe estar entre 1 y 6"
        return True, silo, ""
    except ValueError:
        return False, 0, "Debe ingresar un número de silo válido (1-6)"

# PASO 1: Cédula
@dp.message(RegistroState.medicion_cedula)
async def medicion_get_cedula(message: types.Message, state: FSMContext):
    """Captura y valida la cédula del operario"""
    cedula = message.text.strip()

    if not validar_cedula_sitio3(cedula):
        await message.answer(
            "⚠️ Cédula inválida.\n\n"
            "Debe contener solo números y tener entre 6 y 12 dígitos.\n\n"
            "Por favor, intente nuevamente:"
        )
        return

    await state.update_data(medicion_cedula=cedula)
    await message.answer(
        f"📋 Cédula ingresada: *{cedula}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.medicion_confirmar_cedula)

@dp.message(RegistroState.medicion_confirmar_cedula, F.text == "1")
async def medicion_confirmar_cedula_si(message: types.Message, state: FSMContext):
    """Confirma cédula y pasa a selección de silo"""
    data = await state.get_data()
    cedula = data.get('medicion_cedula')
    telegram_user_id = message.from_user.id

    hay_alerta, cedulas_previas = await verificar_multiples_cedulas(telegram_user_id, cedula)

    if hay_alerta:
        username = message.from_user.username
        if username:
            username = f"@{username}"
        else:
            first_name = message.from_user.first_name or ""
            last_name = message.from_user.last_name or ""
            username = f"{first_name} {last_name}".strip() or "Sin nombre"

        await enviar_alerta_seguridad(
            telegram_user_id=telegram_user_id,
            username=username,
            cedula_actual=cedula,
            cedulas_previas=cedulas_previas,
            tipo_operacion="Ingreso concentrado en silo"
        )

    # Crear teclado con opciones de silos
    builder = ReplyKeyboardBuilder()
    builder.button(text="1")
    builder.button(text="2")
    builder.button(text="3")
    builder.button(text="4")
    builder.button(text="5")
    builder.button(text="6")
    builder.adjust(3)  # 3 botones por fila

    await message.answer(
        "📦 *Selección de Silo*\n\n"
        "La granja tiene 6 silos disponibles.\n"
        "Seleccione UN silo para registrar el ingreso:\n\n"
        "Escriba el número del silo (1-6):",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.medicion_seleccion_silos)

@dp.message(RegistroState.medicion_confirmar_cedula, F.text == "2")
async def medicion_confirmar_cedula_no(message: types.Message, state: FSMContext):
    """Rechaza cédula y vuelve a preguntar"""
    await message.answer("¿Cuál es su cédula?")
    await state.set_state(RegistroState.medicion_cedula)

@dp.message(RegistroState.medicion_confirmar_cedula)
async def medicion_confirmar_cedula_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 2: Selección de UN solo silo
@dp.message(RegistroState.medicion_seleccion_silos, F.text.in_(["1", "2", "3", "4", "5", "6"]))
async def medicion_get_silo(message: types.Message, state: FSMContext):
    """Captura y valida selección de silo único"""
    seleccion = message.text.strip()

    es_valido, silo, mensaje_error = validar_silo_unico(seleccion)

    if not es_valido:
        await message.answer(
            f"⚠️ {mensaje_error}\n\n"
            "Por favor seleccione un silo del 1 al 6:",
            parse_mode="Markdown"
        )
        return

    await state.update_data(medicion_silo_actual=silo)

    await message.answer(
        f"📦 Silo seleccionado: *Silo {silo}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.medicion_confirmar_silos)

@dp.message(RegistroState.medicion_seleccion_silos)
async def medicion_silo_invalido(message: types.Message, state: FSMContext):
    """Handler para selección de silo inválida"""
    await message.answer("⚠️ Por favor seleccione un silo válido (1-6) usando los botones.")

@dp.message(RegistroState.medicion_confirmar_silos, F.text == "1")
async def medicion_confirmar_silo_si(message: types.Message, state: FSMContext):
    """Confirma silo y pasa a tipo de alimento"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="Levante")
    builder.button(text="Engorde / Levante 3 medicado")
    builder.button(text="Finalizador")
    builder.adjust(1)

    await message.answer(
        "🍽️ ¿Qué tipo de alimento va a ingresar?",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.medicion_tipo_comida)

@dp.message(RegistroState.medicion_confirmar_silos, F.text == "2")
async def medicion_confirmar_silo_no(message: types.Message, state: FSMContext):
    """Rechaza silo y vuelve a preguntar"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="1")
    builder.button(text="2")
    builder.button(text="3")
    builder.button(text="4")
    builder.button(text="5")
    builder.button(text="6")
    builder.adjust(3)

    await message.answer(
        "📦 *Selección de Silo*\n\n"
        "Escriba el número del silo (1-6):",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.medicion_seleccion_silos)

@dp.message(RegistroState.medicion_confirmar_silos)
async def medicion_confirmar_silo_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 3: Tipo de Alimento
@dp.message(RegistroState.medicion_tipo_comida, F.text.in_(["Levante", "Engorde / Levante 3 medicado", "Finalizador"]))
async def medicion_get_tipo_comida(message: types.Message, state: FSMContext):
    """Captura tipo de alimento y pasa a peso de descargue"""
    tipo_comida = message.text
    await state.update_data(medicion_tipo_comida=tipo_comida)

    data = await state.get_data()
    silo = data.get('medicion_silo_actual')

    await message.answer(
        f"⚖️ *Peso de descargue - Silo {silo}*\n\n"
        "Ingrese el peso en KILOS\n"
        "_(máximo 25,000 kg)_\n\n"
        "*Ejemplos:* 5000, 12500, 8000.5\n\n"
        "Peso en kilos:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.medicion_peso_descargue)

@dp.message(RegistroState.medicion_tipo_comida)
async def medicion_tipo_comida_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

# PASO 4: Peso de Descargue
@dp.message(RegistroState.medicion_peso_descargue)
async def medicion_get_peso_descargue(message: types.Message, state: FSMContext):
    """Captura y valida peso de descargue en kilos"""
    peso_texto = message.text.strip()

    es_valido, peso, mensaje_error = validar_peso_kilos_descargue(peso_texto)

    if not es_valido:
        await message.answer(
            f"⚠️ {mensaje_error}\n\n"
            "Por favor, intente nuevamente:\n"
            "_(máximo 25,000 kg)_",
            parse_mode="Markdown"
        )
        return

    await state.update_data(medicion_peso_descargue=peso)

    data = await state.get_data()
    silo = data.get('medicion_silo_actual')

    await message.answer(
        f"⚖️ Silo {silo} - Peso de descargue:\n"
        f"*{peso:,.2f} kilos*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.medicion_confirmar_peso_descargue)

@dp.message(RegistroState.medicion_confirmar_peso_descargue, F.text == "1")
async def medicion_confirmar_peso_si(message: types.Message, state: FSMContext):
    """Confirma peso y solicita foto de factura"""
    await message.answer(
        "📸 Por favor envíe una FOTO de la *FACTURA DE ALIMENTO*",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.medicion_foto_factura)

@dp.message(RegistroState.medicion_confirmar_peso_descargue, F.text == "2")
async def medicion_confirmar_peso_no(message: types.Message, state: FSMContext):
    """Rechaza peso y vuelve a preguntar"""
    data = await state.get_data()
    silo = data.get('medicion_silo_actual')

    await message.answer(
        f"⚖️ *Peso de descargue - Silo {silo}*\n\n"
        "Ingrese el peso en KILOS\n"
        "_(máximo 25,000 kg)_\n\n"
        "Peso en kilos:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.medicion_peso_descargue)

@dp.message(RegistroState.medicion_confirmar_peso_descargue)
async def medicion_confirmar_peso_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor escriba 1 para confirmar o 2 para editar.")

# PASO 5: Foto de Factura
@dp.message(RegistroState.medicion_foto_factura, F.photo)
async def medicion_guardar_foto_factura(message: types.Message, state: FSMContext):
    """Guarda foto de factura y procesa el registro"""
    data = await state.get_data()
    silo = data.get('medicion_silo_actual')

    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)

        images_folder = "imagenes_pesajes"
        if not os.path.exists(images_folder):
            os.makedirs(images_folder)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        cedula = data.get('medicion_cedula', 'SIN_CEDULA')
        file_name = f"factura_silo{silo}_{cedula}_{timestamp}.jpg"
        local_file_path = os.path.join(images_folder, file_name)

        await bot.download_file(file_info.file_path, destination=local_file_path)
        absolute_path = os.path.abspath(local_file_path)

        drive_link = None
        if GOOGLE_CREDENTIALS_PATH and os.path.exists(GOOGLE_CREDENTIALS_PATH) and GOOGLE_FOLDER_ID:
            drive_link = upload_to_drive(local_file_path, file_name)

        if not drive_link:
            drive_link = absolute_path

        # Guardar datos del registro
        silo_data = {
            'numero': silo,
            'tipo_comida': data.get('medicion_tipo_comida'),
            'peso_descargue': data.get('medicion_peso_descargue'),
            'foto_factura': drive_link,
            'foto_local': local_file_path
        }

        silos_procesados = data.get('medicion_silos_procesados', [])
        silos_procesados.append(silo_data)
        await state.update_data(medicion_silos_procesados=silos_procesados)

        # Mostrar resumen y preguntar si quiere agregar otro silo
        resumen = f"✅ *Silo {silo} registrado correctamente*\n\n"
        resumen += "📊 *Resumen hasta ahora:*\n\n"
        for s in silos_procesados:
            resumen += f"✅ Silo {s['numero']}: {s['peso_descargue']:,.2f} kg - {s['tipo_comida']}\n"

        resumen += f"\n📝 *Total de silos registrados: {len(silos_procesados)}*"

        await message.answer(resumen, parse_mode="Markdown")

        builder = ReplyKeyboardBuilder()
        builder.button(text="✅ Sí, otro silo")
        builder.button(text="❌ No, finalizar")
        builder.adjust(2)

        await message.answer(
            "¿Desea registrar otro silo?",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        await state.set_state(RegistroState.medicion_agregar_mas)

    except Exception as e:
        print(f"❌ Error guardando foto de factura: {e}")
        await message.answer("❌ Error al guardar la foto. Por favor, intente nuevamente.")

@dp.message(RegistroState.medicion_foto_factura)
async def medicion_foto_factura_invalida(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor envíe una FOTO de la factura (no texto).")

# PASO 6: Agregar más silos o finalizar
@dp.message(RegistroState.medicion_agregar_mas, F.text.in_(["✅ Sí, otro silo", "Sí", "Si", "1"]))
async def medicion_agregar_otro_silo(message: types.Message, state: FSMContext):
    """Usuario quiere agregar otro silo"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="1")
    builder.button(text="2")
    builder.button(text="3")
    builder.button(text="4")
    builder.button(text="5")
    builder.button(text="6")
    builder.adjust(3)

    await message.answer(
        "📦 *Selección de Silo*\n\n"
        "Escriba el número del silo (1-6):",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.medicion_seleccion_silos)

@dp.message(RegistroState.medicion_agregar_mas, F.text.in_(["❌ No, finalizar", "No", "2"]))
async def medicion_finalizar_registro(message: types.Message, state: FSMContext):
    """Usuario finaliza el registro - Guardar en BD y notificar"""
    await message.answer("⏳ Guardando registros...", reply_markup=types.ReplyKeyboardRemove())

    data = await state.get_data()
    cedula = data.get('medicion_cedula')
    silos_procesados = data.get('medicion_silos_procesados', [])
    session_id = data.get('medicion_session_id')

    if not silos_procesados:
        await message.answer("⚠️ No hay silos registrados para guardar.")
        await volver_menu_sitio3(message, state)
        return

    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            fecha_registro = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            telegram_user_id = message.from_user.id

            for silo in silos_procesados:
                await conn.execute('''
                    INSERT INTO operario_sitio3_medicion_silos
                    (cedula_operario, numero_silo, tipo_comida, peso_antes, peso_despues, diferencia,
                     foto_antes, foto_despues, fecha_registro, session_id, telegram_user_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ''', cedula, silo['numero'], silo['tipo_comida'], 0.0,
                    silo['peso_descargue'] / 1000,  # Convertir kg a toneladas para compatibilidad
                    silo['peso_descargue'] / 1000,
                    None,  # No hay foto antes
                    silo['foto_factura'],  # Foto de factura va en foto_despues
                    fecha_registro, session_id, telegram_user_id)

            print(f"✅ {len(silos_procesados)} silos guardados en BD (session: {session_id})")
        else:
            print("⚠️ No se pudo obtener conexión a la base de datos")

    except Exception as e:
        print(f"❌ Error guardando en base de datos: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

    # Calcular total
    total_kilos = sum(s['peso_descargue'] for s in silos_procesados)

    # Enviar notificación al grupo
    if GROUP_CHAT_ID:
        try:
            fecha_formateada = datetime.now().strftime('%d/%m/%Y %H:%M')

            mensaje_grupo = (
                "📦 *NUEVO INGRESO DE CONCENTRADO EN SILO - SITIO 3*\n"
                "#Sitio3 #IngresoConcentrado\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Operario: `{cedula}`\n"
                f"🕒 Fecha: {fecha_formateada}\n\n"
                "📊 *SILOS REGISTRADOS:*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )

            for silo in silos_procesados:
                mensaje_grupo += (
                    f"🔹 *SILO {silo['numero']}*\n"
                    f"   Tipo: {silo['tipo_comida']}\n"
                    f"   Peso: {silo['peso_descargue']:,.2f} kg\n\n"
                )

            mensaje_grupo += (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏋️ *TOTAL INGRESADO: {total_kilos:,.2f} kilos*"
            )

            await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")

            # Enviar fotos de facturas
            for silo in silos_procesados:
                try:
                    foto_path = silo.get('foto_local')
                    if foto_path and os.path.exists(foto_path):
                        with open(foto_path, 'rb') as photo:
                            await bot.send_photo(
                                chat_id=GROUP_CHAT_ID,
                                photo=types.BufferedInputFile(photo.read(), filename=f"factura_silo{silo['numero']}.jpg"),
                                caption=f"📸 Factura Silo {silo['numero']} - {silo['peso_descargue']:,.2f} kg"
                            )
                except Exception as e_foto:
                    print(f"⚠️ Error enviando foto del Silo {silo['numero']}: {e_foto}")

            print("✅ Notificación y fotos enviadas al grupo")

        except Exception as e:
            print(f"⚠️ Error al enviar notificación al grupo: {e}")

    # Mostrar resumen al usuario
    resumen_usuario = (
        "✅ *Ingreso de concentrado registrado exitosamente*\n\n"
        "📊 *Resumen:*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• Silos procesados: {len(silos_procesados)}\n"
        f"• Total ingresado: *{total_kilos:,.2f} kg*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "¡Felicidades! Ha registrado correctamente la información."
    )

    await message.answer(resumen_usuario, parse_mode="Markdown")

    await asyncio.sleep(1)
    await finalizar_flujo(message, state)

@dp.message(RegistroState.medicion_agregar_mas)
async def medicion_agregar_mas_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

# ==================== FIN INGRESO CONCENTRADO EN SILO ==================== #

# ==================== SUBOPCIÓN 4: REGISTRO CELDAS DE CARGA ==================== #

@dp.message(RegistroState.celdas_cedula)
async def celdas_get_cedula(message: types.Message, state: FSMContext):
    """Obtener cédula del operario para registro de celdas de carga"""
    if not validar_cedula(message.text):
        await message.answer("⚠️ Ingrese solo números (sin letras ni símbolos).")
        return

    await state.update_data(celdas_cedula=message.text)

    await message.answer(
        f"📋 Cédula ingresada: *{message.text}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.celdas_confirmar_cedula)

@dp.message(RegistroState.celdas_confirmar_cedula, F.text == "1")
async def celdas_confirmar_cedula_si(message: types.Message, state: FSMContext):
    """Confirmar cédula y pasar a selección de silo"""
    builder = ReplyKeyboardBuilder()
    for i in range(1, 7):
        builder.add(types.KeyboardButton(text=str(i)))
    builder.adjust(3)

    await message.answer(
        "🏭 *Selección de Silo*\n\n"
        "Seleccione el número de silo (1-6):",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.celdas_seleccion_silo)

@dp.message(RegistroState.celdas_confirmar_cedula, F.text == "2")
async def celdas_confirmar_cedula_no(message: types.Message, state: FSMContext):
    """Editar cédula"""
    await message.answer("📋 Ingrese nuevamente su número de cédula:")
    await state.set_state(RegistroState.celdas_cedula)

@dp.message(RegistroState.celdas_confirmar_cedula)
async def celdas_confirmar_cedula_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.celdas_seleccion_silo)
async def celdas_seleccionar_silo(message: types.Message, state: FSMContext):
    """Procesar selección de silo"""
    es_valido, silo, error = validar_silo_unico(message.text)

    if not es_valido:
        await message.answer(f"⚠️ {error}")
        return

    await state.update_data(celdas_silo=silo)

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="1"))
    builder.add(types.KeyboardButton(text="2"))
    builder.adjust(2)

    await message.answer(
        f"🏭 Silo seleccionado: *{silo}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.celdas_confirmar_silo)

@dp.message(RegistroState.celdas_confirmar_silo, F.text == "1")
async def celdas_confirmar_silo_si(message: types.Message, state: FSMContext):
    """Confirmar silo y pedir saldo de celdas"""
    await message.answer(
        "📊 *Saldo de Celdas de Carga*\n\n"
        "Ingrese el saldo de final de día de las celdas del silo:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.celdas_saldo)

@dp.message(RegistroState.celdas_confirmar_silo, F.text == "2")
async def celdas_confirmar_silo_no(message: types.Message, state: FSMContext):
    """Volver a seleccionar silo"""
    builder = ReplyKeyboardBuilder()
    for i in range(1, 7):
        builder.add(types.KeyboardButton(text=str(i)))
    builder.adjust(3)

    await message.answer(
        "🏭 *Selección de Silo*\n\n"
        "Seleccione el número de silo (1-6):",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.celdas_seleccion_silo)

@dp.message(RegistroState.celdas_confirmar_silo)
async def celdas_confirmar_silo_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.celdas_saldo)
async def celdas_get_saldo(message: types.Message, state: FSMContext):
    """Obtener saldo de celdas de carga"""
    saldo = message.text.strip()

    if not saldo:
        await message.answer("⚠️ Por favor ingrese el saldo de las celdas.")
        return

    await state.update_data(celdas_saldo=saldo)

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="1"))
    builder.add(types.KeyboardButton(text="2"))
    builder.adjust(2)

    await message.answer(
        f"📊 Saldo ingresado: *{saldo}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.celdas_confirmar_saldo)

@dp.message(RegistroState.celdas_confirmar_saldo, F.text == "1")
async def celdas_confirmar_saldo_si(message: types.Message, state: FSMContext):
    """Confirmar saldo y pedir foto de celdas"""
    await message.answer(
        "📸 *Foto de Celdas de Carga*\n\n"
        "Por favor, envíe una foto de las celdas de carga:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.celdas_foto)

@dp.message(RegistroState.celdas_confirmar_saldo, F.text == "2")
async def celdas_confirmar_saldo_no(message: types.Message, state: FSMContext):
    """Editar saldo"""
    await message.answer(
        "📊 *Saldo de Celdas de Carga*\n\n"
        "Ingrese nuevamente el saldo de final de día de las celdas del silo:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.celdas_saldo)

@dp.message(RegistroState.celdas_confirmar_saldo)
async def celdas_confirmar_saldo_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.celdas_foto, F.photo)
async def celdas_recibir_foto(message: types.Message, state: FSMContext):
    """Procesar foto de celdas de carga"""
    data = await state.get_data()
    cedula = data.get('celdas_cedula')
    silo = data.get('celdas_silo')
    saldo = data.get('celdas_saldo')

    # Obtener la foto con mejor calidad
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)

    # Guardar foto temporalmente
    foto_path = f"temp_celdas_{message.from_user.id}_{silo}.jpg"
    await bot.download_file(file.file_path, foto_path)

    # Subir a Google Drive
    foto_drive_id = None
    try:
        fecha_actual = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_archivo = f"Celdas_Silo{silo}_{fecha_actual}.jpg"
        foto_drive_id = upload_to_drive(foto_path, nombre_archivo, "image/jpeg")
        if foto_drive_id:
            print(f"✅ Foto de celdas subida a Google Drive: {foto_drive_id}")
    except Exception as e:
        print(f"⚠️ Error subiendo foto a Drive: {e}")

    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            session_id = str(uuid.uuid4())
            await conn.execute('''
                INSERT INTO operario_sitio3_celdas_carga
                (cedula_operario, numero_silo, saldo_celda, foto_celda, session_id, telegram_user_id)
                VALUES ($1, $2, $3, $4, $5, $6)
            ''', cedula, silo, saldo, foto_drive_id, session_id, message.from_user.id)
            print(f"✅ Registro de celdas de carga guardado: Silo {silo}")
    except Exception as e:
        print(f"⚠️ Error guardando registro de celdas: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

    # Enviar notificación al grupo
    if GROUP_CHAT_ID:
        try:
            fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M")
            mensaje_grupo = (
                "📊 *REGISTRO DE CELDAS DE CARGA - SITIO 3*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Cédula: {cedula}\n"
                f"🏭 Silo: {silo}\n"
                f"📊 Saldo: {saldo}\n"
                f"📅 Fecha: {fecha_hora}\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")

            # Enviar foto
            if foto_path and os.path.exists(foto_path):
                with open(foto_path, 'rb') as photo_file:
                    await bot.send_photo(
                        chat_id=GROUP_CHAT_ID,
                        photo=types.BufferedInputFile(photo_file.read(), filename=f"celdas_silo{silo}.jpg"),
                        caption=f"📸 Celdas de Carga - Silo {silo}"
                    )
        except Exception as e:
            print(f"⚠️ Error enviando notificación al grupo: {e}")

    # Limpiar foto temporal
    if foto_path and os.path.exists(foto_path):
        try:
            os.remove(foto_path)
        except:
            pass

    # Preguntar si desea registrar otro silo
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="✅ Sí, registrar otro"))
    builder.add(types.KeyboardButton(text="❌ No, finalizar"))
    builder.adjust(1)

    await message.answer(
        "✅ *Registro de celdas guardado exitosamente*\n\n"
        f"📊 Silo {silo} - Saldo: {saldo}\n\n"
        "¿Desea registrar otro silo?",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.celdas_agregar_mas)

@dp.message(RegistroState.celdas_foto)
async def celdas_foto_invalida(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor envíe una foto de las celdas de carga.")

@dp.message(RegistroState.celdas_agregar_mas, F.text.contains("Sí"))
async def celdas_agregar_mas_si(message: types.Message, state: FSMContext):
    """Registrar otro silo"""
    builder = ReplyKeyboardBuilder()
    for i in range(1, 7):
        builder.add(types.KeyboardButton(text=str(i)))
    builder.adjust(3)

    await message.answer(
        "🏭 *Selección de Silo*\n\n"
        "Seleccione el número de silo (1-6):",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.celdas_seleccion_silo)

@dp.message(RegistroState.celdas_agregar_mas, F.text.contains("No"))
async def celdas_agregar_mas_no(message: types.Message, state: FSMContext):
    """Finalizar registro de celdas"""
    await message.answer(
        "✅ *Registro de celdas de carga completado*",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await asyncio.sleep(1)
    await finalizar_flujo(message, state)

@dp.message(RegistroState.celdas_agregar_mas)
async def celdas_agregar_mas_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

# ==================== FIN REGISTRO CELDAS DE CARGA ==================== #

# ==================== SUBOPCIÓN 5: REGISTRO DE COMBUSTIBLE ==================== #

@dp.message(RegistroState.combustible_cedula)
async def combustible_get_cedula(message: types.Message, state: FSMContext):
    """Obtener cédula del operario para registro de combustible"""
    if not validar_cedula(message.text):
        await message.answer("⚠️ Ingrese solo números (sin letras ni símbolos).")
        return

    await state.update_data(combustible_cedula=message.text)

    await message.answer(
        f"📋 Cédula ingresada: *{message.text}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.combustible_confirmar_cedula)

@dp.message(RegistroState.combustible_confirmar_cedula, F.text == "1")
async def combustible_confirmar_cedula_si(message: types.Message, state: FSMContext):
    """Confirmar cédula y preguntar tipo de combustible"""
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="⛽ Diesel"))
    builder.add(types.KeyboardButton(text="⛽ Gasolina"))
    builder.adjust(2)

    await message.answer(
        "⛽ *Tipo de Combustible*\n\n"
        "Seleccione el tipo de combustible:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.combustible_tipo)

@dp.message(RegistroState.combustible_confirmar_cedula, F.text == "2")
async def combustible_confirmar_cedula_no(message: types.Message, state: FSMContext):
    """Editar cédula"""
    await message.answer("📋 Ingrese nuevamente su número de cédula:")
    await state.set_state(RegistroState.combustible_cedula)

@dp.message(RegistroState.combustible_confirmar_cedula)
async def combustible_confirmar_cedula_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.combustible_tipo)
async def combustible_seleccionar_tipo(message: types.Message, state: FSMContext):
    """Procesar tipo de combustible y mostrar opciones según el tipo"""
    texto = message.text.lower()

    if "diesel" in texto:
        tipo = "Diesel"
        await state.update_data(combustible_tipo=tipo)

        # Opciones para Diesel: Planta 1, Planta 2, Otros
        builder = ReplyKeyboardBuilder()
        builder.add(types.KeyboardButton(text="🔧 Planta 1"))
        builder.add(types.KeyboardButton(text="🔧 Planta 2"))
        builder.add(types.KeyboardButton(text="⚙️ Otros"))
        builder.adjust(2)

        await message.answer(
            "🚜 *¿Qué equipo o maquinaria va a tanquear?*\n\n"
            "Seleccione una opción:",
            parse_mode="Markdown",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        await state.set_state(RegistroState.combustible_equipo)

    elif "gasolina" in texto:
        tipo = "Gasolina"
        await state.update_data(combustible_tipo=tipo)

        # Opciones para Gasolina: Can-am, Vehículos, Equipos
        builder = ReplyKeyboardBuilder()
        builder.add(types.KeyboardButton(text="🏍️ Can-am"))
        builder.add(types.KeyboardButton(text="🚗 Vehículos"))
        builder.add(types.KeyboardButton(text="⚙️ Equipos"))
        builder.adjust(2)

        await message.answer(
            "🚜 *¿Qué equipo o maquinaria va a tanquear?*\n\n"
            "Seleccione una opción:",
            parse_mode="Markdown",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        await state.set_state(RegistroState.combustible_equipo)

    else:
        await message.answer("⚠️ Por favor seleccione Diesel o Gasolina usando los botones.")
        return

@dp.message(RegistroState.combustible_equipo)
async def combustible_seleccionar_equipo(message: types.Message, state: FSMContext):
    """Procesar equipo/maquinaria seleccionado según tipo de combustible"""
    texto = message.text.lower()
    data = await state.get_data()
    tipo_combustible = data.get('combustible_tipo')

    if tipo_combustible == "Diesel":
        # Opciones Diesel: Planta 1, Planta 2, Otros
        if "planta 1" in texto:
            equipo = "Planta 1"
            await state.update_data(combustible_equipo=equipo)
            await mostrar_centro_costo(message, state)
        elif "planta 2" in texto:
            equipo = "Planta 2"
            await state.update_data(combustible_equipo=equipo)
            await mostrar_centro_costo(message, state)
        elif "otros" in texto:
            await state.update_data(combustible_equipo="Otros")
            await message.answer(
                "⚙️ *Nombre del Equipo*\n\n"
                "Por favor escriba el equipo:",
                parse_mode="Markdown",
                reply_markup=types.ReplyKeyboardRemove()
            )
            await state.set_state(RegistroState.combustible_nombre_equipo)
        else:
            await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

    elif tipo_combustible == "Gasolina":
        # Opciones Gasolina: Can-am, Vehículos, Equipos
        if "can-am" in texto or "canam" in texto:
            equipo = "Can-am"
            await state.update_data(combustible_equipo=equipo)
            await mostrar_centro_costo(message, state)
        elif "vehículo" in texto or "vehiculo" in texto:
            await state.update_data(combustible_equipo="Vehículo")
            await message.answer(
                "🚗 *Placa del Vehículo*\n\n"
                "Ingrese la placa del vehículo (formato: ABC123):",
                parse_mode="Markdown",
                reply_markup=types.ReplyKeyboardRemove()
            )
            await state.set_state(RegistroState.combustible_placa)
        elif "equipo" in texto:
            await state.update_data(combustible_equipo="Equipos")
            await message.answer(
                "⚙️ *Nombre del Equipo*\n\n"
                "Por favor escriba el equipo:",
                parse_mode="Markdown",
                reply_markup=types.ReplyKeyboardRemove()
            )
            await state.set_state(RegistroState.combustible_nombre_equipo)
        else:
            await message.answer("⚠️ Por favor seleccione una opción válida usando los botones.")

async def mostrar_centro_costo(message: types.Message, state: FSMContext):
    """Mostrar opciones de centro de costo"""
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🐷 Porcicultura"))
    builder.add(types.KeyboardButton(text="🌾 Finca"))
    builder.add(types.KeyboardButton(text="🏢 Administrativo"))
    builder.add(types.KeyboardButton(text="🏝️ Manakao"))
    builder.add(types.KeyboardButton(text="📤 Externo"))
    builder.adjust(2)

    await message.answer(
        "📍 *¿Qué centro de costo (área) es?*\n\n"
        "Seleccione una opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.combustible_centro_costo)

@dp.message(RegistroState.combustible_placa)
async def combustible_get_placa(message: types.Message, state: FSMContext):
    """Obtener y validar placa del vehículo"""
    placa = message.text.strip().upper()

    # Usar la validación existente de placas
    if not validar_placa(placa):
        await message.answer(
            "⚠️ Placa inválida.\n\n"
            "El formato debe ser: 3 letras + 3 números\n"
            "Ejemplo: ABC123\n\n"
            "Por favor ingrese la placa nuevamente:"
        )
        return

    await state.update_data(combustible_placa=placa)

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="1"))
    builder.add(types.KeyboardButton(text="2"))
    builder.adjust(2)

    await message.answer(
        f"🚗 Placa ingresada: *{placa}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.combustible_confirmar_placa)

@dp.message(RegistroState.combustible_confirmar_placa, F.text == "1")
async def combustible_confirmar_placa_si(message: types.Message, state: FSMContext):
    """Confirmar placa y pasar a centro de costo"""
    await mostrar_centro_costo(message, state)

@dp.message(RegistroState.combustible_confirmar_placa, F.text == "2")
async def combustible_confirmar_placa_no(message: types.Message, state: FSMContext):
    """Editar placa"""
    await message.answer(
        "🚗 *Placa del Vehículo*\n\n"
        "Ingrese nuevamente la placa del vehículo (formato: ABC123):",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.combustible_placa)

@dp.message(RegistroState.combustible_confirmar_placa)
async def combustible_confirmar_placa_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.combustible_nombre_equipo)
async def combustible_get_nombre_equipo(message: types.Message, state: FSMContext):
    """Obtener nombre del equipo"""
    nombre = message.text.strip()

    if len(nombre) < 2:
        await message.answer("⚠️ Ingrese un nombre válido para el equipo.")
        return

    await state.update_data(combustible_nombre_equipo=nombre)

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="1"))
    builder.add(types.KeyboardButton(text="2"))
    builder.adjust(2)

    await message.answer(
        f"⚙️ Equipo ingresado: *{nombre}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.combustible_confirmar_nombre_equipo)

@dp.message(RegistroState.combustible_confirmar_nombre_equipo, F.text == "1")
async def combustible_confirmar_nombre_si(message: types.Message, state: FSMContext):
    """Confirmar nombre y pasar a centro de costo"""
    await mostrar_centro_costo(message, state)

@dp.message(RegistroState.combustible_confirmar_nombre_equipo, F.text == "2")
async def combustible_confirmar_nombre_no(message: types.Message, state: FSMContext):
    """Editar nombre del equipo"""
    await message.answer(
        "⚙️ *Nombre del Equipo*\n\n"
        "Por favor escriba el equipo:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.combustible_nombre_equipo)

@dp.message(RegistroState.combustible_confirmar_nombre_equipo)
async def combustible_confirmar_nombre_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.combustible_centro_costo)
async def combustible_seleccionar_centro(message: types.Message, state: FSMContext):
    """Procesar centro de costo"""
    texto = message.text.lower()

    if "porcicultura" in texto:
        centro = "Porcicultura"
    elif "finca" in texto:
        centro = "Finca"
    elif "administrativo" in texto:
        centro = "Administrativo"
    elif "manakao" in texto:
        centro = "Manakao"
    elif "externo" in texto:
        centro = "Externo"
    else:
        await message.answer("⚠️ Por favor seleccione un centro de costo válido usando los botones.")
        return

    await state.update_data(combustible_centro_costo=centro)

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="1"))
    builder.add(types.KeyboardButton(text="2"))
    builder.adjust(2)

    await message.answer(
        f"📍 Centro de costo: *{centro}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.combustible_confirmar_centro_costo)

@dp.message(RegistroState.combustible_confirmar_centro_costo, F.text == "1")
async def combustible_confirmar_centro_si(message: types.Message, state: FSMContext):
    """Confirmar centro de costo y guardar registro"""
    data = await state.get_data()
    cedula = data.get('combustible_cedula')
    tipo = data.get('combustible_tipo')
    equipo = data.get('combustible_equipo')
    placa = data.get('combustible_placa')
    nombre_equipo = data.get('combustible_nombre_equipo')
    centro_costo = data.get('combustible_centro_costo')

    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            session_id = str(uuid.uuid4())
            await conn.execute('''
                INSERT INTO operario_sitio3_combustible
                (cedula_operario, tipo_combustible, equipo_maquinaria, placa_vehiculo, nombre_equipo, centro_costo, session_id, telegram_user_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ''', cedula, tipo, equipo, placa, nombre_equipo, centro_costo, session_id, message.from_user.id)
            print(f"✅ Registro de combustible guardado")
    except Exception as e:
        print(f"⚠️ Error guardando registro de combustible: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

    # Enviar notificación al grupo
    if GROUP_CHAT_ID:
        try:
            fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M")

            # Construir detalles del equipo
            if equipo == "Vehículo" and placa:
                detalle_equipo = f"🚗 Vehículo - Placa: {placa}"
            elif nombre_equipo:
                detalle_equipo = f"⚙️ {equipo}: {nombre_equipo}"
            else:
                detalle_equipo = f"⚙️ {equipo}"

            mensaje_grupo = (
                "⛽ *REGISTRO DE COMBUSTIBLE - SITIO 3*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Cédula: {cedula}\n"
                f"⛽ Tipo: {tipo}\n"
                f"{detalle_equipo}\n"
                f"📍 Centro de Costo: {centro_costo}\n"
                f"📅 Fecha: {fecha_hora}\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")
        except Exception as e:
            print(f"⚠️ Error enviando notificación al grupo: {e}")

    # Mostrar resumen al usuario
    if equipo == "Vehículo" and placa:
        detalle = f"Vehículo - Placa: {placa}"
    elif nombre_equipo:
        detalle = f"{equipo}: {nombre_equipo}"
    else:
        detalle = equipo

    resumen = (
        "✅ *Registro de combustible guardado exitosamente*\n\n"
        "📊 *Resumen:*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• Tipo: {tipo}\n"
        f"• Equipo: {detalle}\n"
        f"• Centro de costo: {centro_costo}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    await message.answer(resumen, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    await asyncio.sleep(1)
    await finalizar_flujo(message, state)

@dp.message(RegistroState.combustible_confirmar_centro_costo, F.text == "2")
async def combustible_confirmar_centro_no(message: types.Message, state: FSMContext):
    """Volver a seleccionar centro de costo"""
    await mostrar_centro_costo(message, state)

@dp.message(RegistroState.combustible_confirmar_centro_costo)
async def combustible_confirmar_centro_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

# ==================== FIN REGISTRO DE COMBUSTIBLE ==================== #

# ==================== SUBOPCIÓN 6: TRASLADO ENTRE CORRALES ==================== #

@dp.message(RegistroState.traslado_cedula)
async def traslado_get_cedula(message: types.Message, state: FSMContext):
    """Obtener cédula del operario para traslado entre corrales"""
    if not validar_cedula(message.text):
        await message.answer("⚠️ Ingrese solo números (sin letras ni símbolos).")
        return

    await state.update_data(traslado_cedula=message.text)

    await message.answer(
        f"📋 Cédula ingresada: *{message.text}*\n\n"
        "¿Es correcta?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.traslado_confirmar_cedula)

@dp.message(RegistroState.traslado_confirmar_cedula, F.text == "1")
async def traslado_confirmar_cedula_si(message: types.Message, state: FSMContext):
    """Confirmar cédula y pedir corral de origen"""
    await message.answer(
        "🐷 *Corral de Origen*\n\n"
        "Ingrese el número de corral de donde salen los animales:",
        parse_mode="Markdown"
    )
    await state.set_state(RegistroState.traslado_corral_origen)

@dp.message(RegistroState.traslado_confirmar_cedula, F.text == "2")
async def traslado_confirmar_cedula_no(message: types.Message, state: FSMContext):
    """Editar cédula"""
    await message.answer("📋 Ingrese nuevamente su número de cédula:")
    await state.set_state(RegistroState.traslado_cedula)

@dp.message(RegistroState.traslado_confirmar_cedula)
async def traslado_confirmar_cedula_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.traslado_corral_origen)
async def traslado_get_corral_origen(message: types.Message, state: FSMContext):
    """Obtener corral de origen"""
    corral = message.text.strip()

    if not corral:
        await message.answer("⚠️ Por favor ingrese el número de corral.")
        return

    await state.update_data(traslado_corral_origen=corral)

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="1"))
    builder.add(types.KeyboardButton(text="2"))
    builder.adjust(2)

    await message.answer(
        f"🐷 Corral de origen: *{corral}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.traslado_confirmar_origen)

@dp.message(RegistroState.traslado_confirmar_origen, F.text == "1")
async def traslado_confirmar_origen_si(message: types.Message, state: FSMContext):
    """Confirmar origen y pedir corral de destino"""
    await message.answer(
        "🐷 *Corral de Destino*\n\n"
        "Ingrese el número de corral de destino:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.traslado_corral_destino)

@dp.message(RegistroState.traslado_confirmar_origen, F.text == "2")
async def traslado_confirmar_origen_no(message: types.Message, state: FSMContext):
    """Editar corral de origen"""
    await message.answer(
        "🐷 *Corral de Origen*\n\n"
        "Ingrese nuevamente el número de corral de donde salen los animales:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.traslado_corral_origen)

@dp.message(RegistroState.traslado_confirmar_origen)
async def traslado_confirmar_origen_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

@dp.message(RegistroState.traslado_corral_destino)
async def traslado_get_corral_destino(message: types.Message, state: FSMContext):
    """Obtener corral de destino"""
    corral = message.text.strip()

    if not corral:
        await message.answer("⚠️ Por favor ingrese el número de corral.")
        return

    data = await state.get_data()
    corral_origen = data.get('traslado_corral_origen')

    if corral == corral_origen:
        await message.answer("⚠️ El corral de destino no puede ser igual al corral de origen.")
        return

    await state.update_data(traslado_corral_destino=corral)

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="1"))
    builder.add(types.KeyboardButton(text="2"))
    builder.adjust(2)

    await message.answer(
        f"🐷 Corral de destino: *{corral}*\n\n"
        "¿Es correcto?\n\n"
        "1️⃣ Sí, confirmar\n"
        "2️⃣ No, editar\n\n"
        "Escriba el número de la opción:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(RegistroState.traslado_confirmar_destino)

@dp.message(RegistroState.traslado_confirmar_destino, F.text == "1")
async def traslado_confirmar_destino_si(message: types.Message, state: FSMContext):
    """Confirmar destino y guardar traslado"""
    data = await state.get_data()
    cedula = data.get('traslado_cedula')
    corral_origen = data.get('traslado_corral_origen')
    corral_destino = data.get('traslado_corral_destino')

    # Guardar en base de datos
    conn = None
    try:
        conn = await get_db_connection()
        if conn:
            session_id = str(uuid.uuid4())
            await conn.execute('''
                INSERT INTO operario_sitio3_traslado_corrales
                (cedula_operario, corral_origen, corral_destino, session_id, telegram_user_id)
                VALUES ($1, $2, $3, $4, $5)
            ''', cedula, corral_origen, corral_destino, session_id, message.from_user.id)
            print(f"✅ Traslado entre corrales guardado: {corral_origen} -> {corral_destino}")
    except Exception as e:
        print(f"⚠️ Error guardando traslado: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await release_db_connection(conn)

    # Enviar notificación al grupo
    if GROUP_CHAT_ID:
        try:
            fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M")
            mensaje_grupo = (
                "🔄 *TRASLADO ENTRE CORRALES - SITIO 3*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Cédula: {cedula}\n"
                f"📤 Corral origen: {corral_origen}\n"
                f"📥 Corral destino: {corral_destino}\n"
                f"📅 Fecha: {fecha_hora}\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            await bot.send_message(GROUP_CHAT_ID, mensaje_grupo, parse_mode="Markdown")
        except Exception as e:
            print(f"⚠️ Error enviando notificación al grupo: {e}")

    # Mostrar resumen al usuario
    resumen = (
        "✅ *Traslado registrado exitosamente*\n\n"
        "📊 *Resumen:*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• Corral origen: {corral_origen}\n"
        f"• Corral destino: {corral_destino}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    await message.answer(resumen, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    await asyncio.sleep(1)
    await finalizar_flujo(message, state)

@dp.message(RegistroState.traslado_confirmar_destino, F.text == "2")
async def traslado_confirmar_destino_no(message: types.Message, state: FSMContext):
    """Editar corral de destino"""
    await message.answer(
        "🐷 *Corral de Destino*\n\n"
        "Ingrese nuevamente el número de corral de destino:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistroState.traslado_corral_destino)

@dp.message(RegistroState.traslado_confirmar_destino)
async def traslado_confirmar_destino_invalido(message: types.Message, state: FSMContext):
    await message.answer("⚠️ Por favor seleccione 1 o 2.")

# ==================== FIN TRASLADO ENTRE CORRALES ==================== #

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

    # Finalizar flujo de consulta
    await finalizar_flujo(message, state)

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

    # Finalizar flujo
    await finalizar_flujo(message, state)

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

        # NUEVA: Alerta al grupo si discrepancia > 100 kg
        if diferencia > 100 and GROUP_CHAT_ID:
            try:
                mensaje_alerta = (
                    "🚨 *ALERTA DE DISCREPANCIA* 🚨\n"
                    "#Discrepancia\n\n"
                    f"⚠️ Diferencia detectada: *{diferencia:.2f} kg*\n\n"
                    f"📊 Peso báscula: *{peso_bascula:,.2f} kg*\n"
                    f"📊 Total silos: *{total_silos:,.2f} kg*\n"
                    f"🚛 Placa: *{data.get('camion', 'N/A')}*\n"
                    f"👤 Cédula: *{data.get('cedula', 'N/A')}*\n\n"
                    "⚠️ Se requiere verificación"
                )
                await bot.send_message(GROUP_CHAT_ID, mensaje_alerta, parse_mode="Markdown")
                print(f"✅ Alerta de discrepancia enviada al grupo ({diferencia:.2f} kg)")
            except Exception as e:
                print(f"⚠️ Error enviando alerta de discrepancia: {e}")
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

        # Finalizar flujo
        await finalizar_flujo(message, state)

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



