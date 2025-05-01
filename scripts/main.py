import os
import json
import io
import re
import sys
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ========= CONFIGURAÇÕES =========
SCOPES = [
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly"
]
PORT = 8080
REDIRECT_URI = f'http://localhost:{PORT}/'

# Estrutura de diretórios
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Diretório raiz do projeto
WORK_DIR = os.path.join(BASE_DIR, 'trabalhos')
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))  # Pasta scripts

# ========= FUNÇÕES AUXILIARES =========
def sanitizar_nome(nome):
    """Remove caracteres inválidos para nomes de arquivos/pastas"""
    return re.sub(r'[\\/*?:"<>|]', "_", nome.strip())

def determinar_periodo(data_obj):
    """Define o termo e bimestre com base na data"""
    mes = data_obj.month
    if 2 <= mes <= 3:
        return ("1 Termo", "1 Bimestre")
    elif 4 <= mes <= 5:
        return ("1 Termo", "2 Bimestre")
    elif 6 <= mes <= 7:
        return ("2 Termo", "3 Bimestre")
    elif 8 <= mes <= 11:
        return ("2 Termo", "4 Bimestre")
    else:
        return ("Outros", f"Bimestre {mes//2}")

# ========= AUTENTICAÇÃO =========
def configurar_credenciais():
    """Cria o arquivo credentials.json dinamicamente"""
    creds_path = os.path.join(SCRIPTS_DIR, 'credentials.json')
    if not os.path.exists(creds_path):
        with open(creds_path, 'w') as f:
            json.dump({
                "web": {
                    "client_id": "AAAAAAAAAAAAAAAAAAAAAAAAAA",
                    "project_id": "BBBBBBBBBB",
                    "client_secret": "CCCCCCCCCCCCCCCCCCCC",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": [REDIRECT_URI]
                }
            }, f)

def autenticar():
    """Gerencia todo o fluxo de autenticação"""
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    
    token_path = os.path.join(SCRIPTS_DIR, 'token.json')
    creds = None
    
    # Tentar carregar token existente
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    # Renovar token se necessário
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                os.path.join(SCRIPTS_DIR, 'credentials.json'),
                SCOPES,
                redirect_uri=REDIRECT_URI
            )
            creds = flow.run_local_server(
                port=PORT,
                authorization_prompt_message='Autorize o aplicativo neste link: {url}',
                success_message='Autenticação concluída! Pode fechar o navegador.',
                open_browser=True
            )
        
        # Salvar novo token
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    
    return creds

# ========= DOWNLOAD E ORGANIZAÇÃO =========
def baixar_arquivo(drive_service, file_id, caminho_completo):
    """Baixa e converte arquivos do Google Drive"""
    try:
        # Obter metadados do arquivo
        metadata = drive_service.files().get(
            fileId=file_id,
            fields="mimeType,name"
        ).execute()

        mime_type = metadata['mimeType']
        nome_arquivo = sanitizar_nome(metadata['name'])

        # Mapear conversões
        conversoes = {
            'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
            'application/vnd.google-apps.spreadsheet': 
                ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
            'application/vnd.google-apps.presentation': ('application/pdf', '.pdf'),
            'application/vnd.google-apps.form': ('application/zip', '.zip'),
            'default': (None, '')
        }

        # Determinar tipo de download
        export_mime, extensao = conversoes.get(mime_type, conversoes['default'])
        if not caminho_completo.endswith(extensao):
            caminho_completo += extensao

        # Configurar requisição
        if export_mime:
            request = drive_service.files().export_media(
                fileId=file_id,
                mimeType=export_mime
            )
            print(f"📝 Convertendo: {nome_arquivo} -> {extensao}")
        else:
            request = drive_service.files().get_media(fileId=file_id)
            print(f"⬇️ Baixando: {nome_arquivo}")

        # Executar download
        fh = io.FileIO(caminho_completo, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"   Progresso: {int(status.progress() * 100)}%")
        
        return True

    except Exception as e:
        print(f"❌ Erro em {nome_arquivo}: {str(e)}")
        return False

def criar_estrutura_pastas(curso_nome, data_entrega):
    """Cria a hierarquia de pastas adequada"""
    try:
        data_obj = datetime(
            year=data_entrega['year'],
            month=data_entrega['month'],
            day=data_entrega['day']
        )
        termo, bimestre = determinar_periodo(data_obj)
        data_str = data_obj.strftime('%d-%m-%Y')
    except:
        termo, bimestre, data_str = "Sem Periodo", "Sem Data", "sem-data"

    caminho = os.path.join(
        WORK_DIR,
        termo,
        bimestre,
        sanitizar_nome(curso_nome),
        data_str
    )
    
    os.makedirs(caminho, exist_ok=True)
    return caminho

def processar_curso(classroom, drive, curso):
    """Processa todas as atividades de um curso"""
    curso_id = curso['id']
    curso_nome = sanitizar_nome(curso['name'])
    
    print(f"\n📘 Processando: {curso_nome}")
    
    try:
        atividades = classroom.courses().courseWork().list(
            courseId=curso_id
        ).execute().get('courseWork', [])
    except Exception as e:
        print(f"❌ Erro ao buscar atividades: {str(e)}")
        return

    for atividade in atividades:
        processar_atividade(classroom, drive, curso_id, atividade, curso_nome)

def processar_atividade(classroom, drive, curso_id, atividade, curso_nome):
    """Processa uma atividade individual"""
    atividade_id = atividade['id']
    atividade_titulo = sanitizar_nome(atividade['title'])
    data_entrega = atividade.get('dueDate', {})

    # Criar estrutura de pastas
    try:
        pasta_destino = criar_estrutura_pastas(curso_nome, data_entrega)
    except Exception as e:
        print(f"❌ Erro ao criar pasta: {str(e)}")
        return

    # Processar submissões
    try:
        submissoes = classroom.courses().courseWork().studentSubmissions().list(
            courseId=curso_id,
            courseWorkId=atividade_id
        ).execute().get('studentSubmissions', [])
    except Exception as e:
        print(f"❌ Erro ao buscar submissões: {str(e)}")
        return

    for submissao in submissoes:
        if submissao['state'] in ('TURNED_IN', 'RETURNED'):
            processar_submissao(drive, submissao, pasta_destino, atividade_titulo)
        elif submissao['state'] == 'CREATED':
            print(f"  ⚠️ Pendente: {atividade_titulo}")

def processar_submissao(drive, submissao, pasta_destino, atividade_titulo):
    """Processa os anexos de uma submissão"""
    if 'assignmentSubmission' not in submissao:
        return

    for anexo in submissao['assignmentSubmission'].get('attachments', []):
        if 'driveFile' in anexo:
            arquivo = anexo['driveFile']
            nome_arquivo = sanitizar_nome(arquivo['title'])
            caminho_completo = os.path.join(
                pasta_destino,
                f"{atividade_titulo} - {nome_arquivo}"
            )
            
            if not os.path.exists(caminho_completo):
                baixar_arquivo(drive, arquivo['id'], caminho_completo)

# ========= EXECUÇÃO PRINCIPAL =========
def main():
    # Verificar diretório correto
    if not os.path.basename(SCRIPTS_DIR) == 'scripts':
        print("❌ Execute o script da pasta 'scripts'")
        sys.exit(1)

    # Configurar ambiente
    configurar_credenciais()
    os.makedirs(WORK_DIR, exist_ok=True)
    
    # Autenticar
    try:
        creds = autenticar()
    except Exception as e:
        print(f"❌ Falha na autenticação: {str(e)}")
        sys.exit(1)

    # Iniciar serviços
    try:
        classroom = build('classroom', 'v1', credentials=creds)
        drive = build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"❌ Falha ao iniciar serviços: {str(e)}")
        sys.exit(1)

    # Obter cursos
    try:
        cursos = classroom.courses().list().execute().get('courses', [])
    except Exception as e:
        print(f"❌ Falha ao listar cursos: {str(e)}")
        sys.exit(1)

    # Processar cursos
    print("\n📚 Iniciando organização de trabalhos...")
    for curso in cursos:
        processar_curso(classroom, drive, curso)
    
    print("\n✅ Organização concluída! Verifique a pasta 'trabalhos'")

if __name__ == '__main__':
    main()
