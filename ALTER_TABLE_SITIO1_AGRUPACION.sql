-- Script para agregar la columna lechones_por_grupo a la tabla operario_fijo_granja
-- Ejecutar en Railway PostgreSQL console o DBeaver

-- Agregar la columna lechones_por_grupo (puede ser NULL para registros antiguos)
ALTER TABLE operario_fijo_granja
ADD COLUMN IF NOT EXISTS lechones_por_grupo INTEGER;

-- Verificar que el cambio funcionó
SELECT id, cedula, cantidad_lechones, lechones_por_grupo, peso_total, fecha
FROM operario_fijo_granja
ORDER BY fecha DESC
LIMIT 5;

-- NOTA: Los registros antiguos (antes de este cambio) tendrán NULL en lechones_por_grupo
-- Los nuevos registros tendrán el valor de agrupación (5, 10, etc.)
