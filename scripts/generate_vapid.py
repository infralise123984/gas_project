"""
Script para generar claves VAPID para Web Push
Ejecutar una vez y guardar las claves en .env
"""
from py_vapid import Vapid

def generate_vapid_keys():
    """Genera un nuevo par de claves VAPID"""
    v = Vapid()
    v.generate_keys()
    v.save_key('private_key.pem')
    v.save_public_key('public_key.pem')
    
    # Obtener claves en formato base64url para usar en la app
    import base64
    from cryptography.hazmat.primitives import serialization
    
    # Leer la clave privada en formato PEM
    with open('private_key.pem', 'rb') as f:
        private_pem = f.read()
    
    # Leer la clave pública
    with open('public_key.pem', 'rb') as f:
        public_pem = f.read()
    
    # Cargar las claves y convertirlas
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.backends import default_backend
    
    private_key = serialization.load_pem_private_key(private_pem, password=None, backend=default_backend())
    public_key = private_key.public_key()
    
    # Exportar clave pública en formato sin comprimir
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    
    # Exportar clave privada en formato raw (32 bytes)
    private_bytes = private_key.private_numbers().private_value.to_bytes(32, byteorder='big')
    
    # Convertir a base64url (sin padding)
    def to_base64url(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')
    
    public_key_b64 = to_base64url(public_bytes)
    private_key_b64 = to_base64url(private_bytes)
    
    print("\n" + "="*60)
    print("CLAVES VAPID GENERADAS - AGREGAR A .env")
    print("="*60)
    print(f"\nVAPID_PUBLIC_KEY={public_key_b64}")
    print(f"VAPID_PRIVATE_KEY={private_key_b64}")
    print(f"VAPID_ADMIN_EMAIL=mailto:admin@kimgas.cl")
    print("\n" + "="*60)
    
    # Limpiar archivos temporales
    import os
    os.remove('private_key.pem')
    os.remove('public_key.pem')
    
    return public_key_b64, private_key_b64

if __name__ == '__main__':
    generate_vapid_keys()
