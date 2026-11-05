# Portal do Dízimo 2027 – Eu Faço Parte (Flask)

Portal público + pedido de materiais + acompanhamento por protocolo + área da Coordenação (login e indicadores).

## Rodar no seu computador
```
pip install -r requirements.txt
python app.py
```
Abre em http://127.0.0.1:5000 (usa SQLite local). Coordenação: http://127.0.0.1:5000/coord
Usuário `coordenacao`, senha `dizimo2027` (troque em *Usuários*).

## Publicar (GitHub + Render + Neon)
1. **Neon**: crie um projeto e copie a *connection string* (começa com `postgresql://`).
2. **GitHub**: crie um repositório e envie o conteúdo desta pasta (Add file → Upload files).
3. **Render**: New → Web Service → escolha o repositório.
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
4. Em *Environment* adicione:
   - `DATABASE_URL` = a string do Neon
   - `SECRET_KEY` = qualquer texto longo e aleatório
   - `COORD_USER` = login da coordenação (opcional, padrão `coordenacao`)
   - `COORD_PASSWORD` = senha inicial (se omitir, a senha será `dizimo2027` e o painel avisará para trocar)
5. **Avisos por e-mail (opcional, recomendado):** no Render adicione também
   - `SMTP_HOST` = `smtp.gmail.com` · `SMTP_PORT` = `587`
   - `SMTP_USER` = o Gmail que enviará os avisos
   - `SMTP_PASSWORD` = *senha de app* do Google (Conta Google → Segurança → Verificação em duas etapas → Senhas de app). Não use a senha normal.
   - `MAIL_TO` = e-mail do padre (pode ter vários, separados por vírgula)
   - `MAIL_FROM` = opcional (por padrão, o mesmo de `SMTP_USER`)
   Depois, em *Usuários → Enviar e-mail de teste*, confirme que chegou.
   Quem recebe: a Coordenação (`MAIL_TO`) a cada pedido novo, alteração ou cancelamento; a paróquia, se informou e-mail, quando o pedido é enviado, alterado, cancelado ou muda de situação.
6. As tabelas, as 43 paróquias e o catálogo 2027 são criados sozinhos no primeiro acesso.

## Estrutura
- `app.py` rotas, modelos e regras · `seed_data.py` paróquias, histórico 2026, catálogo e modelos P/M/G
- `templates/` telas públicas · `templates/coord/` área da Coordenação · `static/` CSS, JS e imagens
- Imagens ficam em arquivos (`static/img`), não no banco.

## Regras
- Protocolo `DZ27-{decanato}{nn}-{4 letras}`; total sempre recalculado no servidor; preço do item é gravado no pedido.
- Paróquia altera/cancela só em *Rascunho/Enviado*, com protocolo + WhatsApp. Depois, só a Coordenação.
- Catálogo e preços editáveis em *Preços*.
