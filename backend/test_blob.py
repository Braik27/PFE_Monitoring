import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

conn = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
print('CONN_STR presente:', bool(conn))

if not conn:
    print('AZURE_STORAGE_CONNECTION_STRING non definie dans .env !')
    sys.exit(1)

print('Debut:', conn[:50], '...')

from azure.storage.blob import BlobServiceClient, ContentSettings
client = BlobServiceClient.from_connection_string(conn)
container = client.get_container_client('flux-results')

try:
    props = container.get_container_properties()
    print('Conteneur OK:', props.name)
except Exception as e:
    print('Conteneur ERREUR:', e)
    sys.exit(1)

try:
    container.upload_blob(
        name='CUSTOMERBALANCE/test.json',
        data='{"test": true}',
        overwrite=True
    )
    print('Upload test REUSSI ! Verifiez Azure.')
except Exception as e:
    print('Upload ERREUR:', e)