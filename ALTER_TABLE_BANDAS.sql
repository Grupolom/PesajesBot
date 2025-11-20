-- Script para cambiar la columna cantidad_animales a bandas (tipo TEXT) en Railway
-- Ejecutar en Railway PostgreSQL console

-- Opción 1: Si la tabla NO tiene datos importantes (borra todos los datos)
-- DROP TABLE IF EXISTS operario_sitio3_animales CASCADE;
-- CREATE TABLE operario_sitio3_animales (
--     id SERIAL PRIMARY KEY,
--     cedula_operario VARCHAR(20) NOT NULL,
--     bandas VARCHAR(50) NOT NULL,  -- NUEVA COLUMNA
--     rango_corrales VARCHAR(50) NOT NULL,
--     tipo_comida VARCHAR(50) NOT NULL,
--     fecha_registro TIMESTAMP DEFAULT NOW(),
--     session_id UUID NOT NULL,
--     telegram_user_id BIGINT NOT NULL
-- );

-- Opción 2: Si la tabla tiene datos (preserva los datos existentes)
-- PASO 1: Agregar nueva columna bandas como TEXT
ALTER TABLE operario_sitio3_animales
ADD COLUMN IF NOT EXISTS bandas VARCHAR(50);

-- PASO 2: Copiar datos de cantidad_animales a bandas (convertir números a texto)
UPDATE operario_sitio3_animales
SET bandas = CAST(cantidad_animales AS VARCHAR)
WHERE bandas IS NULL;

-- PASO 3: Eliminar columna antigua cantidad_animales
ALTER TABLE operario_sitio3_animales
DROP COLUMN IF EXISTS cantidad_animales;

-- Verificar que el cambio funcionó
SELECT * FROM operario_sitio3_animales LIMIT 5;
