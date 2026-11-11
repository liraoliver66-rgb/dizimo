"""Portal do Dízimo 2027 – Campanha Regional "Eu Faço Parte" (Região Episcopal Brasilândia).
Aplicação Flask única: portal público + pedido de materiais + acompanhamento + área da Coordenação."""
import os, re, io, csv, json, time, secrets, string, smtplib, threading
import urllib.request, urllib.error
from urllib.parse import quote
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, session, flash,
                   abort, Response, send_file, jsonify)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

import seed_data as sd

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-troque-esta-chave"
uri = os.environ.get("DATABASE_URL", "sqlite:///dizimo27.db")
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = uri
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
db = SQLAlchemy(app)

STATUS = ["Rascunho", "Enviado", "Em conferência", "Confirmado", "Encaminhado à gráfica",
          "Em produção", "Pronto para retirada", "Entregue", "Cancelado"]
EDITAVEL = ["Rascunho", "Enviado"]          # paróquia só altera nesses status
ATIVOS = [s for s in STATUS if s not in ("Rascunho", "Cancelado")]
PAGAMENTOS = ["Pendente", "Parcial", "Pago"]
STATUS_COR = {"Rascunho": "gray", "Enviado": "blue", "Em conferência": "amber", "Confirmado": "green",
              "Encaminhado à gráfica": "violet", "Em produção": "violet", "Pronto para retirada": "teal",
              "Entregue": "green", "Cancelado": "red"}


# ----------------------------------------------------------------- modelos
class Paroquia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    decanato = db.Column(db.String(2), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    nome = db.Column(db.String(120), nullable=False)
    hist = db.Column(db.Text, default="{}")      # histórico 2026 (JSON)
    gasto26 = db.Column(db.Integer, default=0)   # centavos

    @property
    def h(self):
        return json.loads(self.hist or "{}")

    @property
    def decanato_nome(self):
        return sd.DECANATOS[self.decanato]


class Produto(db.Model):
    id = db.Column(db.String(30), primary_key=True)
    kit = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(160), nullable=False)
    desc = db.Column(db.String(300), default="")
    preco = db.Column(db.Integer, nullable=False)       # centavos
    status = db.Column(db.String(20), default="orcado")  # orcado | confirmar
    step = db.Column(db.Integer, default=1)
    ativo = db.Column(db.Boolean, default=True)
    ordem = db.Column(db.Integer, default=0)


class Pedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    protocolo = db.Column(db.String(30), unique=True, nullable=False)
    paroquia_id = db.Column(db.Integer, db.ForeignKey("paroquia.id"), nullable=False)
    paroquia = db.relationship("Paroquia")
    responsavel = db.Column(db.String(120), nullable=False)
    whatsapp = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), default="")
    status = db.Column(db.String(30), default="Enviado")
    pagamento = db.Column(db.String(10), default="Pendente")
    pago = db.Column(db.Integer, default=0)              # centavos já recebidos
    obs = db.Column(db.Text, default="")
    obs_coord = db.Column(db.Text, default="")
    grafica_em = db.Column(db.DateTime)
    pronto_em = db.Column(db.DateTime)
    retirado_em = db.Column(db.DateTime)
    criado = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    itens = db.relationship("Item", cascade="all, delete-orphan", backref="pedido")

    @property
    def total(self):
        return sum(i.qtd * i.preco for i in self.itens)

    @property
    def saldo(self):
        return max(self.total - (self.pago or 0), 0)

    @property
    def editavel(self):
        return self.status in EDITAVEL


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedido.id"), nullable=False)
    produto_id = db.Column(db.String(30), nullable=False)
    nome = db.Column(db.String(160), nullable=False)     # snapshot
    qtd = db.Column(db.Integer, nullable=False)
    preco = db.Column(db.Integer, nullable=False)        # snapshot (centavos)


class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False)
    login = db.Column(db.String(40), unique=True, nullable=False)
    senha = db.Column(db.String(255), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    trocar_senha = db.Column(db.Boolean, default=False)


# ----------------------------------------------------------------- utilidades
def brl(c):
    v = f"{(c or 0) / 100:,.2f}"
    return "R$ " + v.replace(",", "X").replace(".", ",").replace("X", ".")


def milhar(n):
    return f"{int(n or 0):,}".replace(",", ".")


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def fmt_zap(s):
    d = so_digitos(s)
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return s


def dt(d):
    return d.strftime("%d/%m/%Y %H:%M") if d else "—"


def wa_link(numero, texto):
    """Link de WhatsApp com mensagem pronta (abre o app; quem clica só confirma o envio)."""
    d = so_digitos(numero)
    if not d.startswith("55"):
        d = "55" + d
    return f"https://wa.me/{d}?text={quote(texto)}"


app.jinja_env.filters.update(brl=brl, milhar=milhar, zap=fmt_zap, dt=dt)
app.jinja_env.globals["wa_link"] = wa_link


@app.context_processor
def ctx():
    # 'a' = parâmetros da URL; fica sempre definido para qualquer template.
    # Quando a rota passa a=... explicitamente (ex.: lista de pedidos), o valor da rota prevalece.
    return dict(STATUS=STATUS, STATUS_COR=STATUS_COR, PAGAMENTOS=PAGAMENTOS, DEC=sd.DECANATOS,
                csrf=csrf_token(), usuario_atual=session.get("nome"), a=request.args)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
            abort(400, "Sessão expirada. Volte e tente de novo.")


def gerar_protocolo(p):
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        s = "".join(secrets.choice(alfabeto) for _ in range(4))
        prot = f"DZ27-{p.decanato}{p.numero:02d}-{s}"
        if not Pedido.query.filter_by(protocolo=prot).first():
            return prot


def seed():
    if Paroquia.query.count() == 0:
        cont = {}
        for r in sd.H26:
            cont[r[0]] = cont.get(r[0], 0) + 1
            h = dict(zip(["folder", "banner", "mp", "f1", "f2", "cp", "cg"], r[2:9]))
            db.session.add(Paroquia(decanato=r[0], numero=cont[r[0]], nome=r[1],
                                    hist=json.dumps(h), gasto26=round(r[9] * 100)))
    if Produto.query.count() == 0:
        for i, p in enumerate(sd.PRODUTOS):
            db.session.add(Produto(id=p["id"], kit=p["kit"], nome=p["nome"], desc=p["desc"],
                                   preco=p["preco"], status=p["status"], step=p["step"], ordem=i))
    if Usuario.query.count() == 0:
        senha = os.environ.get("COORD_PASSWORD")
        db.session.add(Usuario(nome="Coordenação", login=os.environ.get("COORD_USER", "coordenacao"),
                               senha=generate_password_hash(senha or "dizimo2027"),
                               trocar_senha=not senha))
    db.session.commit()


with app.app_context():
    db.create_all()
    seed()


# ----------------------------------------------------------------- e-mail
def mail_config():
    return dict(host=os.environ.get("SMTP_HOST", ""), port=int(os.environ.get("SMTP_PORT", "587") or 587),
                user=os.environ.get("SMTP_USER", ""), senha=os.environ.get("SMTP_PASSWORD", ""),
                de=os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER", ""),
                api=os.environ.get("BREVO_API_KEY", "").strip(),
                para=[e.strip() for e in os.environ.get("MAIL_TO", "").split(",") if e.strip()])


def email_ativo():
    c = mail_config()
    return bool((c["host"] or c["api"]) and c["para"])


def _brevo_enviar(c, dest, assunto, corpo):
    """Envio por API HTTPS (funciona no plano gratuito do Render, que bloqueia SMTP)."""
    if not c["de"]:
        return "Falta MAIL_FROM (o e-mail remetente validado no Brevo)."
    dados = json.dumps({"sender": {"name": "Dízimo 2027", "email": c["de"]},
                        "to": [{"email": e} for e in dest],
                        "subject": assunto, "textContent": corpo}).encode("utf-8")
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=dados, method="POST",
                                 headers={"api-key": c["api"], "content-type": "application/json",
                                          "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return None
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "ignore")[:300]
        app.logger.warning("Brevo recusou o e-mail (%s): %s", e.code, detalhe)
        return f"Brevo recusou ({e.code}): {detalhe}"
    except Exception as e:
        app.logger.warning("Falha ao enviar e-mail pelo Brevo: %s", e)
        return str(e)


def _smtp_enviar(dest, assunto, corpo):
    """Envia agora; devolve None se deu certo ou o texto do erro."""
    c = mail_config()
    if not dest or not (c["host"] or c["api"]):
        return "E-mail não configurado (faltam BREVO_API_KEY ou SMTP_HOST, e MAIL_TO)."
    if c["api"]:
        return _brevo_enviar(c, dest, assunto, corpo)
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = assunto, c["de"], ", ".join(dest)
        msg.set_content(corpo)
        if c["port"] == 465:
            srv = smtplib.SMTP_SSL(c["host"], c["port"], timeout=15)
        else:
            srv = smtplib.SMTP(c["host"], c["port"], timeout=15)
            srv.starttls()
        if c["user"]:
            srv.login(c["user"], c["senha"])
        srv.send_message(msg)
        srv.quit()
        return None
    except Exception as e:  # nunca derruba o pedido por falha de e-mail
        app.logger.warning("Falha ao enviar e-mail: %s", e)
        return str(e)


def enviar_email(dest, assunto, corpo):
    c = mail_config()
    if (c["host"] or c["api"]) and dest:
        threading.Thread(target=_smtp_enviar, args=(dest, assunto, corpo), daemon=True).start()


def resumo_pedido(p):
    linhas = [f"Protocolo: {p.protocolo}", f"Paróquia: {p.paroquia.nome} ({p.paroquia.decanato_nome})",
              f"Responsável: {p.responsavel} · {fmt_zap(p.whatsapp)}", f"Situação: {p.status} · Pagamento: {p.pagamento}", ""]
    linhas += [f"- {i.qtd}x {i.nome} ({brl(i.qtd * i.preco)})" for i in p.itens]
    linhas += ["", f"Total: {brl(p.total)}"]
    return "\n".join(linhas)


def avisar(p, evento, paroquia=False):
    """evento: texto curto. Sempre avisa a Coordenação; avisa a paróquia se pedido e se tiver e-mail."""
    base = request.host_url.rstrip("/")
    c = mail_config()
    corpo = f"{evento}\n\n{resumo_pedido(p)}\n\n"
    enviar_email(c["para"], f"[Dízimo 2027] {evento} · {p.paroquia.nome}",
                 corpo + f"Abrir na Coordenação: {base}/coord/pedidos/{p.id}")
    if paroquia and p.email:
        enviar_email([p.email], f"Dízimo 2027 · {evento} · {p.protocolo}",
                     f"Olá, {p.responsavel}!\n\n{evento}\n\n{resumo_pedido(p)}\n\n"
                     f"Acompanhe com o protocolo e o WhatsApp em {base}/acompanhar\n\nCampanha Eu Faço Parte")


# ----------------------------------------------------------------- site público
def catalogo():
    prods = Produto.query.filter_by(ativo=True).order_by(Produto.ordem).all()
    return [(k, n, [p for p in prods if p.kit == k]) for k, n in sd.KITS]


def ler_itens(prods):
    """Lê quantidades do formulário e recalcula tudo no servidor."""
    itens = []
    for p in prods:
        try:
            q = int(request.form.get(f"q_{p.id}", "0") or 0)
        except ValueError:
            q = 0
        if q > 0:
            itens.append(Item(produto_id=p.id, nome=p.nome, qtd=min(q, 100000), preco=p.preco))
    return itens


def hist_json(pars):
    return json.dumps({p.id: p.h for p in pars})


@app.route("/")
def home():
    feitos = {p.paroquia_id for p in pedidos_validos()}
    pars = Paroquia.query.order_by(Paroquia.decanato, Paroquia.numero).all()
    grupos = [(n, [(p.nome, p.id in feitos) for p in pars if p.decanato == k]) for k, n in sd.DECANATOS.items()]
    return render_template("home.html", kits=catalogo(), aderiram=len(feitos), total_par=len(pars), grupos=grupos)


def ultimo_pedido_meu():
    """Último pedido (não cancelado) feito ou acompanhado neste navegador."""
    prots = session.get("meus", [])
    if not prots:
        return None
    return (Pedido.query.filter(Pedido.protocolo.in_(prots), Pedido.status != "Cancelado")
            .order_by(Pedido.criado.desc()).first())


@app.route("/pedido", methods=["GET", "POST"])
def pedido():
    pars = Paroquia.query.order_by(Paroquia.decanato, Paroquia.numero).all()
    prods = Produto.query.filter_by(ativo=True).all()
    if request.method == "POST":
        par = db.session.get(Paroquia, int(request.form.get("paroquia") or 0))
        resp = request.form.get("responsavel", "").strip()
        zap = so_digitos(request.form.get("whatsapp"))
        itens = ler_itens(prods)
        erros = []
        if not par: erros.append("Escolha a paróquia.")
        if len(resp) < 3: erros.append("Informe o nome do responsável.")
        if len(zap) not in (10, 11): erros.append("Informe o WhatsApp com DDD.")
        if not itens: erros.append("Escolha ao menos um item.")
        em = request.form.get("email", "").strip()
        if em and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em): erros.append("O e-mail parece incompleto. Corrija ou deixe em branco.")
        if erros:
            for e in erros: flash(e, "erro")
            return render_template("pedido.html", pars=pars, kits=catalogo(), modelos=sd.MODELOS,
                                   f=request.form, ped=None, ultimo=ultimo_pedido_meu(),
                                   hist_json=hist_json(pars)), 400
        p = Pedido(protocolo=gerar_protocolo(par), paroquia=par, responsavel=resp, whatsapp=zap,
                   email=request.form.get("email", "").strip()[:120], obs=request.form.get("obs", "")[:1000],
                   itens=itens)
        db.session.add(p)
        db.session.commit()
        session.setdefault("meus", [])
        session["meus"] = session["meus"] + [p.protocolo]
        avisar(p, "Novo pedido recebido", paroquia=True)
        return redirect(url_for("comprovante", prot=p.protocolo))
    ultimo = ultimo_pedido_meu()
    f = {}
    if request.args.get("repetir") and ultimo:
        f = {"paroquia": ultimo.paroquia_id, "responsavel": ultimo.responsavel,
             "whatsapp": ultimo.whatsapp, "email": ultimo.email,
             **{f"q_{i.produto_id}": i.qtd for i in ultimo.itens}}
        flash("Preenchemos com os dados do seu último pedido. Confira e envie.", "ok")
    return render_template("pedido.html", pars=pars, kits=catalogo(), modelos=sd.MODELOS, f=f,
                           ped=None, ultimo=ultimo, hist_json=hist_json(pars))


def pedido_do_dono(prot):
    p = Pedido.query.filter_by(protocolo=prot.upper()).first_or_404()
    if prot.upper() not in session.get("meus", []) and not session.get("uid"):
        abort(403)
    return p


@app.route("/pedido/<prot>/editar", methods=["GET", "POST"])
def editar(prot):
    p = pedido_do_dono(prot)
    if not p.editavel and not session.get("uid"):
        flash("Este pedido já está em conferência e não pode mais ser alterado. Fale com a Coordenação.", "erro")
        return redirect(url_for("comprovante", prot=p.protocolo))
    prods = Produto.query.all()
    pars = Paroquia.query.order_by(Paroquia.decanato, Paroquia.numero).all()
    if request.method == "POST":
        itens = ler_itens(prods)
        if not itens:
            flash("Escolha ao menos um item.", "erro")
        else:
            p.itens[:] = itens
            p.responsavel = request.form.get("responsavel", p.responsavel).strip() or p.responsavel
            zap = so_digitos(request.form.get("whatsapp"))
            if len(zap) in (10, 11): p.whatsapp = zap
            p.email = request.form.get("email", "").strip()[:120]
            p.obs = request.form.get("obs", "")[:1000]
            db.session.commit()
            flash("Pedido atualizado.", "ok")
            avisar(p, "Pedido alterado", paroquia=True)
            return redirect(url_for("comprovante", prot=p.protocolo))
    f = {"paroquia": p.paroquia_id, "responsavel": p.responsavel, "whatsapp": p.whatsapp,
         "email": p.email, "obs": p.obs, **{f"q_{i.produto_id}": i.qtd for i in p.itens}}
    return render_template("pedido.html", pars=pars, kits=catalogo(), modelos=sd.MODELOS, f=f, ped=p, hist_json=hist_json(pars))


@app.route("/pedido/<prot>/cancelar", methods=["POST"])
def cancelar(prot):
    p = pedido_do_dono(prot)
    if p.editavel:
        p.status = "Cancelado"
        db.session.commit()
        flash("Pedido cancelado.", "ok")
        avisar(p, "Pedido cancelado", paroquia=True)
    return redirect(url_for("comprovante", prot=p.protocolo))


@app.route("/comprovante/<prot>")
def comprovante(prot):
    p = pedido_do_dono(prot)
    return render_template("comprovante.html", p=p)


@app.route("/acompanhar", methods=["GET", "POST"])
def acompanhar():
    if request.method == "POST":
        prot = request.form.get("protocolo", "").strip().upper()
        zap = so_digitos(request.form.get("whatsapp"))
        p = Pedido.query.filter_by(protocolo=prot).first()
        if p and zap and p.whatsapp == zap:
            session["meus"] = list(set(session.get("meus", []) + [p.protocolo]))
            return redirect(url_for("comprovante", prot=p.protocolo))
        flash("Protocolo ou WhatsApp não conferem.", "erro")
    return render_template("acompanhar.html")


# ----------------------------------------------------------------- coordenação
FALHAS = {}   # ip -> (contagem, bloqueado_até)


def coord_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w


@app.route("/coord/login", methods=["GET", "POST"])
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if request.method == "POST":
        c, ate = FALHAS.get(ip, (0, 0))
        if ate > time.time():
            flash("Muitas tentativas. Aguarde alguns minutos.", "erro")
            return render_template("coord/login.html"), 429
        u = Usuario.query.filter_by(login=request.form.get("login", "").strip().lower(), ativo=True).first()
        if u and check_password_hash(u.senha, request.form.get("senha", "")):
            FALHAS.pop(ip, None)
            session.clear()
            session.permanent = True
            session.update(uid=u.id, nome=u.nome)
            if u.trocar_senha:
                flash("Troque a senha padrão em Usuários.", "erro")
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/coord") else url_for("dashboard"))
        c += 1
        FALHAS[ip] = (c, time.time() + 300 if c >= 5 else 0)
        flash("Usuário ou senha incorretos.", "erro")
    return render_template("coord/login.html")


@app.route("/coord/sair")
def logout():
    session.clear()
    return redirect(url_for("login"))


def pedidos_validos():
    return Pedido.query.filter(Pedido.status.notin_(["Rascunho", "Cancelado"])).all()


def qtd_grupo(p):
    """Quantidades 2027 comparáveis com 2026."""
    d = {"folder": 0, "folheto": 0, "cartaz": 0}
    for i in p.itens:
        if i.produto_id in d: d[i.produto_id] += i.qtd
    return d


@app.route("/coord")
@coord_required
def dashboard():
    peds = pedidos_validos()
    pars = Paroquia.query.all()
    total = sum(p.total for p in peds)
    pago = sum(p.pago or 0 for p in peds)
    com_pedido = {p.paroquia_id for p in peds}
    por_status = {s: 0 for s in STATUS if s not in ("Rascunho", "Cancelado")}
    for p in peds: por_status[p.status] += 1
    dec = {}
    for k, n in sd.DECANATOS.items():
        ps = [x for x in pars if x.decanato == k]
        pe = [p for p in peds if p.paroquia.decanato == k]
        dec[k] = dict(nome=n, par=len(ps), com=len({p.paroquia_id for p in pe}),
                      valor=sum(p.total for p in pe), pago=sum(p.pago or 0 for p in pe), pedidos=len(pe))
    prod = {}
    for p in peds:
        for i in p.itens:
            d = prod.setdefault(i.produto_id, dict(nome=i.nome, qtd=0, valor=0))
            d["qtd"] += i.qtd; d["valor"] += i.qtd * i.preco
    top = sorted(prod.values(), key=lambda x: -x["valor"])[:8]
    # comparativo 2026 x 2027 (folder, folheto, cartaz)
    c26 = {"folder": sum(x.h.get("folder", 0) for x in pars), "folheto": sum(x.h.get("f1", 0) for x in pars),
           "cartaz": sum(x.h.get("cp", 0) + x.h.get("cg", 0) for x in pars)}
    c27 = {"folder": 0, "folheto": 0, "cartaz": 0}
    for p in peds:
        for k, v in qtd_grupo(p).items(): c27[k] += v
    etapas = dict(
        pagto=sum(1 for p in peds if p.pagamento == "Pago"),
        grafica=sum(1 for p in peds if p.grafica_em), pronto=sum(1 for p in peds if p.pronto_em),
        retirado=sum(1 for p in peds if p.retirado_em))
    recentes = Pedido.query.order_by(Pedido.criado.desc()).limit(6).all()
    return render_template("coord/dashboard.html", n=len(peds), total=total, pago=pago, receber=max(total - pago, 0),
                           adesao=len(com_pedido), npar=len(pars), por_status=por_status, dec=dec, top=top,
                           c26=c26, c27=c27, etapas=etapas, recentes=recentes,
                           gasto26=sum(x.gasto26 for x in pars), sem_senha=bool(Usuario.query.filter_by(
                               id=session["uid"], trocar_senha=True).first()))


def filtrar():
    q = Pedido.query.join(Paroquia)
    st, de, pg, txt = (request.args.get(k, "") for k in ("status", "dec", "pag", "q"))
    if st: q = q.filter(Pedido.status == st)
    if de: q = q.filter(Paroquia.decanato == de)
    if pg: q = q.filter(Pedido.pagamento == pg)
    if txt:
        like = f"%{txt}%"
        q = q.filter((Paroquia.nome.ilike(like)) | (Pedido.protocolo.ilike(like)) | (Pedido.responsavel.ilike(like)))
    return q.order_by(Pedido.criado.desc())


@app.route("/coord/pedidos")
@coord_required
def pedidos():
    lista = filtrar().all()
    return render_template("coord/pedidos.html", lista=lista, a=request.args,
                           total=sum(p.total for p in lista), recebido=sum(p.pago or 0 for p in lista),
                           npagos=sum(1 for p in lista if p.pagamento == "Pago"))


@app.route("/coord/pedidos/acao", methods=["POST"])
@coord_required
def acao_massa():
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    acao = request.form.get("acao", "")
    if not acao:
        flash("Escolha uma ação antes de aplicar.", "erro")
        return redirect(request.referrer or url_for("pedidos"))
    if not ids:
        flash("Selecione ao menos um pedido.", "erro")
        return redirect(request.referrer or url_for("pedidos"))
    ps = Pedido.query.filter(Pedido.id.in_(ids)).all()
    mudaram = []
    for p in ps:
        antes = (p.status, p.pagamento)
        if acao in STATUS: aplicar_status(p, acao)
        elif acao == "pago": p.pagamento, p.pago = "Pago", p.total
        elif acao == "pendente": p.pagamento, p.pago = "Pendente", 0
        if (p.status, p.pagamento) != antes:
            mudaram.append(p)
    db.session.commit()
    for p in mudaram:          # só avisa a paróquia quando algo realmente mudou
        if p.email: avisar_paroquia(p)
    flash(f"{len(ps)} pedido(s) atualizado(s).", "ok")
    return redirect(request.referrer or url_for("pedidos"))


def avisar_paroquia(p):
    """Avisa só a paróquia (a Coordenação fez a mudança)."""
    base = request.host_url.rstrip("/")
    enviar_email([p.email], f"Dízimo 2027 · seu pedido agora está: {p.status} · {p.protocolo}",
                 f"Olá, {p.responsavel}!\n\nSeu pedido {p.protocolo} agora está: {p.status} (pagamento: {p.pagamento}).\n\n"
                 f"{resumo_pedido(p)}\n\nAcompanhe em {base}/acompanhar\n\nCampanha Eu Faço Parte")


def aplicar_status(p, s):
    p.status = s
    agora = datetime.utcnow()
    if s == "Encaminhado à gráfica" and not p.grafica_em: p.grafica_em = agora
    if s == "Pronto para retirada" and not p.pronto_em: p.pronto_em = agora
    if s == "Entregue" and not p.retirado_em: p.retirado_em = agora


@app.route("/coord/pedidos/<int:pid>/pago", methods=["POST"])
@coord_required
def alternar_pago(pid):
    p = db.get_or_404(Pedido, pid)
    if p.pagamento == "Pago":
        p.pagamento, p.pago = "Pendente", 0
    else:
        p.pagamento, p.pago = "Pago", p.total
    db.session.commit()
    if p.email: avisar_paroquia(p)
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/coord/pedidos/<int:pid>", methods=["GET", "POST"])
@coord_required
def pedido_detalhe(pid):
    p = db.get_or_404(Pedido, pid)
    if request.method == "POST":
        s = request.form.get("status")
        antes = (p.status, p.pagamento)
        if s in STATUS and s != p.status: aplicar_status(p, s)
        pg = request.form.get("pagamento")
        if pg in PAGAMENTOS: p.pagamento = pg
        try:
            p.pago = max(round(float(request.form.get("pago", "0").replace(",", ".")) * 100), 0)
        except ValueError:
            pass
        if p.pagamento == "Pago" and p.pago < p.total: p.pago = p.total
        if p.pagamento == "Pendente": p.pago = 0
        p.obs_coord = request.form.get("obs_coord", "")[:2000]
        db.session.commit()
        flash("Pedido atualizado.", "ok")
        if (p.status, p.pagamento) != antes and p.email:
            avisar_paroquia(p)
        return redirect(url_for("pedido_detalhe", pid=pid))
    return render_template("coord/pedido_detalhe.html", p=p)


def planilha(nome, cab, linhas):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = nome[:30]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3A5F")
    for l in linhas: ws.append(l)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 50)
    b = io.BytesIO(); wb.save(b); b.seek(0)
    return send_file(b, as_attachment=True, download_name=f"{nome}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/coord/exportar")
@coord_required
def exportar():
    lista = filtrar().all()
    cab = ["Protocolo", "Decanato", "Paróquia", "Responsável", "WhatsApp", "Status", "Pagamento",
           "Total (R$)", "Pago (R$)", "Itens", "Criado em"]
    linhas = [[p.protocolo, p.paroquia.decanato_nome, p.paroquia.nome, p.responsavel, fmt_zap(p.whatsapp),
               p.status, p.pagamento, p.total / 100, (p.pago or 0) / 100,
               "; ".join(f"{i.qtd}× {i.nome}" for i in p.itens), dt(p.criado)] for p in lista]
    return planilha("pedidos-dizimo-2027", cab, linhas)


def consolidar(status_lista):
    peds = Pedido.query.filter(Pedido.status.in_(status_lista)).all()
    ag = {}
    for p in peds:
        for i in p.itens:
            d = ag.setdefault(i.produto_id, dict(nome=i.nome, qtd=0, valor=0, preco=i.preco, pars=[]))
            d["qtd"] += i.qtd; d["valor"] += i.qtd * i.preco
            d["pars"].append((p.paroquia.nome, i.qtd))
    ordem = {p.id: p.ordem for p in Produto.query.all()}
    return peds, sorted(ag.items(), key=lambda kv: ordem.get(kv[0], 999))


GRAFICA_STATUS = ["Confirmado", "Encaminhado à gráfica", "Em produção", "Pronto para retirada"]


@app.route("/coord/grafica")
@coord_required
def grafica():
    st = request.args.get("status") or None
    peds, ag = consolidar([st] if st in STATUS else GRAFICA_STATUS)
    if request.args.get("xlsx"):
        return planilha("consolidado-grafica", ["Produto", "Quantidade", "Preço unit. (R$)", "Total (R$)"],
                        [[d["nome"], d["qtd"], d["preco"] / 100, d["valor"] / 100] for _, d in ag])
    return render_template("coord/grafica.html", peds=peds, ag=ag, sel=st,
                           total=sum(d["valor"] for _, d in ag), opcoes=GRAFICA_STATUS)


@app.route("/coord/precos", methods=["GET", "POST"])
@coord_required
def precos():
    if request.method == "POST":
        for p in Produto.query.all():
            try:
                p.preco = max(round(float(request.form.get(f"p_{p.id}", "").replace(",", ".")) * 100), 0)
            except ValueError:
                pass
            p.status = request.form.get(f"s_{p.id}", p.status)
            p.ativo = bool(request.form.get(f"a_{p.id}"))
        db.session.commit()
        flash("Catálogo salvo. Pedidos já feitos mantêm o preço da época.", "ok")
        return redirect(url_for("precos"))
    return render_template("coord/precos.html", kits=[(k, n, Produto.query.filter_by(kit=k).order_by(Produto.ordem).all())
                                                       for k, n in sd.KITS])


@app.route("/coord/faltam")
@coord_required
def faltam():
    feitos = {p.paroquia_id for p in pedidos_validos()}
    de = request.args.get("dec", "")
    pars = Paroquia.query.order_by(Paroquia.decanato, Paroquia.numero).all()
    lista = [p for p in pars if p.id not in feitos and (not de or p.decanato == de)]
    if request.args.get("xlsx"):
        return planilha("paroquias-sem-pedido", ["Decanato", "Paróquia", "Gasto 2026 (R$)"],
                        [[p.decanato_nome, p.nome, p.gasto26 / 100] for p in lista])
    return render_template("coord/faltam.html", lista=lista, a=request.args)


@app.route("/coord/usuarios", methods=["GET", "POST"])
@coord_required
def usuarios():
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "novo":
            lg = request.form.get("login", "").strip().lower()
            sn = request.form.get("senha", "")
            if not re.fullmatch(r"[a-z0-9._-]{3,40}", lg) or len(sn) < 8:
                flash("Login (3+ caracteres, sem espaços) e senha (8+ caracteres) são obrigatórios.", "erro")
            elif Usuario.query.filter_by(login=lg).first():
                flash("Esse login já existe.", "erro")
            else:
                db.session.add(Usuario(nome=request.form.get("nome", "").strip() or lg, login=lg,
                                       senha=generate_password_hash(sn)))
                db.session.commit(); flash("Usuário criado.", "ok")
        elif acao == "senha":
            u = db.session.get(Usuario, int(request.form.get("id", 0)))
            sn = request.form.get("senha", "")
            if u and len(sn) >= 8:
                u.senha = generate_password_hash(sn); u.trocar_senha = False
                db.session.commit(); flash("Senha alterada.", "ok")
            else:
                flash("A senha precisa ter 8 caracteres ou mais.", "erro")
        elif acao == "ativar":
            u = db.session.get(Usuario, int(request.form.get("id", 0)))
            if u and u.id != session["uid"]:
                u.ativo = not u.ativo; db.session.commit()
            else:
                flash("Você não pode desativar o próprio usuário.", "erro")
        return redirect(url_for("usuarios"))
    c = mail_config()
    return render_template("coord/usuarios.html", us=Usuario.query.order_by(Usuario.id).all(),
                           mail_ok=email_ativo(), mail_para=c["para"])


@app.route("/coord/email-teste", methods=["POST"])
@coord_required
def email_teste():
    c = mail_config()
    erro = _smtp_enviar(c["para"], "[Dízimo 2027] Teste de e-mail",
                        "Se você recebeu esta mensagem, os avisos por e-mail estão funcionando.")
    flash("E-mail de teste enviado para " + ", ".join(c["para"]) + "." if not erro else f"Não foi possível enviar: {erro}",
          "ok" if not erro else "erro")
    return redirect(url_for("usuarios"))


@app.route("/coord/paroquias")
@coord_required
def paroquias():
    pars = Paroquia.query.order_by(Paroquia.decanato, Paroquia.numero).all()
    peds = {}
    for p in pedidos_validos():
        peds.setdefault(p.paroquia_id, []).append(p)
    linhas = [dict(p=p, peds=peds.get(p.id, []), total=sum(x.total for x in peds.get(p.id, []))) for p in pars]
    return render_template("coord/paroquias.html", linhas=linhas)


@app.route("/coord/paroquias/<int:pid>")
@coord_required
def paroquia_detalhe(pid):
    par = db.get_or_404(Paroquia, pid)
    h = par.h
    historico = [(lbl, h.get(k, 0), sd.P26[k], h.get(k, 0) * sd.P26[k]) for k, lbl in
                 [("folder", "Folder"), ("banner", "Banner"), ("mp", "Marca-página"), ("f1", "Folheto 1"),
                  ("f2", "Folheto 2"), ("cp", "Cartaz pequeno"), ("cg", "Cartaz grande")]]
    peds = Pedido.query.filter_by(paroquia_id=pid).order_by(Pedido.criado.desc()).all()
    return render_template("coord/paroquia_detalhe.html", par=par, historico=historico, peds=peds)


@app.route("/saude")
def saude():
    return "ok"


if __name__ == "__main__":
    app.run(debug=True)
