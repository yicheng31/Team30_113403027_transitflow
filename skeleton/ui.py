"""
TransitFlow — Gradio Web Interface
====================================
Run with:  python skeleton/ui.py
Then open: http://localhost:7860

Optimizations (v2):
  - Welcome message on chat startup
  - Login panel auto-closes after successful login
  - Quick-select station buttons in sidebar
  - Booking confirmation modal
"""

import sys
sys.path.insert(0, ".")

import gradio as gr
from skeleton.agent import run_agent
from skeleton.llm_provider import llm
from skeleton.config import GEMINI_CHAT_MODEL, OLLAMA_CHAT_MODEL
from databases.relational.queries import (
    login_user,
    register_user,
    get_user_secret_question,
    verify_secret_answer,
    update_password,
)

SECRET_QUESTIONS = [
    "What is the name of your first pet?",
    "What is your mother's maiden name?",
    "What city were you born in?",
    "What was the name of your first school?",
    "What is your favourite book?",
    "What was the make of your first car?",
]

# ── Welcome message ────────────────────────────────────────────────────────────

WELCOME_MESSAGE = {
    "role": "assistant",
    "content": (
        "👋 歡迎使用 **TransitFlow** 智慧交通助理！\n\n"
        "我可以幫您：\n"
        "🚂 查詢國鐵班次和票價\n"
        "🚇 查詢捷運路線和票價\n"
        "🗺️ 規劃最快或最便宜的路線\n"
        "🎫 訂票和取消訂票（需登入）\n"
        "📋 查詢退款、行李等相關政策\n\n"
        "請直接輸入您的問題，或點選右側的快速範例開始使用！"
    ),
}

# ── Station quick-select groups ────────────────────────────────────────────────

METRO_STATIONS = [
    ("🚇 中央廣場 MS01", "從中央廣場 (MS01) "),
    ("🚇 河濱站 MS02",   "從河濱站 (MS02) "),
    ("🚇 北門站 MS03",   "從北門站 (MS03) "),
    ("🚇 大學站 MS08",   "從大學站 (MS08) "),
    ("🚇 皇后橋站 MS09", "從皇后橋站 (MS09) "),
    ("🚇 東威克站 MS14", "從東威克站 (MS14) "),
]

RAIL_STATIONS = [
    ("🚂 中央站 NR01",     "從中央站 (NR01) "),
    ("🚂 楓木站 NR02",     "從楓木站 (NR02) "),
    ("🚂 舊城交匯站 NR03", "從舊城交匯站 (NR03) "),
    ("🚂 石港站 NR05",     "從石港站 (NR05) "),
    ("🚂 橋港站 NR06",     "從橋港站 (NR06) "),
    ("🚂 丹摩站 NR09",     "從丹摩站 (NR09) "),
]

# ── Chat handler ───────────────────────────────────────────────────────────────

def chat(user_message: str, history_display: list, agent_history: list,
         show_debug: bool, current_user: str):
    if not user_message.strip():
        return history_display, agent_history, gr.update()

    if show_debug:
        answer, new_agent_history, debug_text = run_agent(
            user_message, agent_history, debug=True, current_user_email=current_user
        )
    else:
        answer, new_agent_history = run_agent(
            user_message, agent_history, debug=False, current_user_email=current_user
        )
        debug_text = ""

    history_display = history_display + [
        {"role": "user",      "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    debug_update = gr.update(value=debug_text, visible=show_debug)
    return history_display, new_agent_history, debug_update


def clear_conversation():
    return [WELCOME_MESSAGE], [], gr.update(value="", visible=False)


# ── Provider / model selection ────────────────────────────────────────────────

_KNOWN_OLLAMA_MODELS = ["llama3.2:1b", "llama3.1:8b"]


def get_ollama_status():
    if llm.ollama_available():
        return "🟢 Ollama is running locally"
    return "🔴 Ollama not detected — install from ollama.com and run `ollama pull " + OLLAMA_CHAT_MODEL + "`"


def get_chat_model_choices() -> list:
    available = set(llm.get_available_ollama_models())
    choices = []
    for m in _KNOWN_OLLAMA_MODELS:
        label = m if m in available else f"{m}  (not pulled)"
        choices.append((label, m))
    choices.append((f"☁️ Gemini ({GEMINI_CHAT_MODEL})", "gemini"))
    return choices


def get_initial_chat_model_value() -> str:
    return "llama3.2:1b"


def on_chat_model_change(value: str):
    if value == "gemini":
        status = llm.set_chat_provider("gemini")
        return f"**Active:** ☁️ Gemini ({GEMINI_CHAT_MODEL})\n\n{status}", get_ollama_status()
    available = set(llm.get_available_ollama_models())
    if value not in available:
        return f"⚠️ `{value}` is not pulled. Run: `ollama pull {value}`", get_ollama_status()
    llm.set_chat_provider("ollama")
    status = llm.set_chat_model(value)
    return f"**Active:** {value}\n\n{status}", get_ollama_status()


# ── Auth handlers ──────────────────────────────────────────────────────────────

def do_login(email: str, password: str):
    if not email.strip() or not password.strip():
        return (
            gr.update(value="請輸入您的 Email 和密碼。", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    user = login_user(email.strip(), password)
    if user is None:
        return (
            gr.update(value="Email 或密碼錯誤，請再試一次。", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    display_name = f"{user['first_name']} {user['surname']}"
    return (
        gr.update(value="", visible=False),
        user["email"],
        gr.update(visible=False),   # login_btn hidden
        gr.update(visible=False),   # register_btn hidden
        gr.update(value=f"**歡迎，{display_name}** 👋", visible=True),
        gr.update(visible=True),    # logout_btn shown
        gr.update(visible=False),   # login_panel auto-closed ✅
    )


def do_logout():
    return (
        None,
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(value="", visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
    )


def do_register(email, first_name, surname, year_of_birth, password, secret_question, secret_answer):
    if not all([
        str(email).strip(), str(first_name).strip(), str(surname).strip(),
        str(password).strip(), secret_question, str(secret_answer).strip(),
    ]):
        return (
            gr.update(value="所有欄位都是必填的，請全部填寫。", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    try:
        year = int(year_of_birth)
        if year < 1900 or year > 2015:
            raise ValueError
    except (ValueError, TypeError):
        return (
            gr.update(value="請輸入有效的出生年份（例如：1990）。", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    ok, err = register_user(
        email.strip(), first_name.strip(), surname.strip(),
        year, password, secret_question, secret_answer.strip(),
    )
    if not ok:
        return (
            gr.update(value=err, visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    display_name = f"{first_name.strip()} {surname.strip()}"
    return (
        gr.update(value="", visible=False),
        email.strip().lower(),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value=f"**歡迎加入，{display_name}** 👋", visible=True),
        gr.update(visible=True),
        gr.update(visible=False),   # register_panel auto-closed ✅
    )


def forgot_find_question(email: str):
    if not email.strip():
        return (
            gr.update(value="請輸入您的 Email 地址。", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    question = get_user_secret_question(email.strip())
    if question is None:
        return (
            gr.update(value="找不到此 Email 的帳號，請確認後再試。", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    return (
        gr.update(value="", visible=False),
        gr.update(value=f"**您的安全問題：** {question}", visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
    )


def forgot_reset_password(email: str, answer: str, new_password: str):
    if not str(answer).strip() or not str(new_password).strip():
        return gr.update(value="請填寫所有欄位。", visible=True)

    if not verify_secret_answer(email.strip(), answer.strip()):
        return gr.update(value="答案不正確，請再試一次。", visible=True)

    if not update_password(email.strip(), new_password):
        return gr.update(value="密碼更新失敗，請稍後再試。", visible=True)

    return gr.update(value="**密碼已成功重設！您現在可以使用新密碼登入。** ✅", visible=True)


# ── Panel visibility toggles ──────────────────────────────────────────────────

def show_login_panel():
    return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

def show_register_panel():
    return gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)

def show_forgot_panel():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=True)

def hide_all_panels():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)


# ── Example queries ────────────────────────────────────────────────────────────

EXAMPLES = [
    "從中央站 (NR01) 到石港站 (NR05) 有哪些國鐵班次？",
    "從中央廣場 (MS01) 到東威克站 (MS14) 最快的捷運路線？",
    "從中央廣場 (MS01) 到石港站 (NR05) 怎麼去？",
    "如果舊城交匯站 (NR03) 關閉，NR01 到 NR05 有什麼替代路線？",
    "我的火車誤點了 45 分鐘，可以申請什麼補償？",
    "攜帶腳踏車搭乘國鐵的政策是什麼？",
]


# ── Build UI ───────────────────────────────────────────────────────────────────

with gr.Blocks(title="TransitFlow", theme=gr.themes.Soft()) as demo:

    # ── Hidden state ──────────────────────────────────────────────────
    agent_history_state = gr.State([])
    current_user_state  = gr.State(None)

    # ── Header ────────────────────────────────────────────────────────
    with gr.Row(equal_height=True):
        gr.Markdown("""
# 🚂 TransitFlow 智慧交通助理
*Powered by PostgreSQL · pgvector · Neo4j · LLM*
        """)
        with gr.Column(scale=0, min_width=240):
            with gr.Row():
                login_btn    = gr.Button("👤 登入",  size="sm", variant="secondary")
                register_btn = gr.Button("📝 註冊",  size="sm", variant="secondary")
            user_info_display = gr.Markdown("", visible=False)
            logout_btn = gr.Button("登出", size="sm", variant="stop", visible=False)

    # ── Login panel ───────────────────────────────────────────────────
    with gr.Column(visible=False) as login_panel:
        gr.Markdown("### 登入")
        login_email_in    = gr.Textbox(label="Email", placeholder="your@email.com")
        login_password_in = gr.Textbox(label="密碼", type="password")
        login_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            login_submit_btn = gr.Button("登入", variant="primary")
            forgot_link_btn  = gr.Button("忘記密碼？", size="sm")
            login_cancel_btn = gr.Button("取消", size="sm")

    # ── Register panel ────────────────────────────────────────────────
    with gr.Column(visible=False) as register_panel:
        gr.Markdown("### 建立帳號")
        with gr.Row():
            reg_first_name_in = gr.Textbox(label="名字")
            reg_surname_in    = gr.Textbox(label="姓氏")
        reg_email_in    = gr.Textbox(label="Email", placeholder="your@email.com")
        reg_year_in     = gr.Textbox(label="出生年份", placeholder="例如：1990")
        reg_password_in = gr.Textbox(label="密碼", type="password")
        reg_question_in = gr.Dropdown(choices=SECRET_QUESTIONS, label="安全問題")
        reg_answer_in   = gr.Textbox(label="安全問題答案")
        reg_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            reg_submit_btn = gr.Button("註冊", variant="primary")
            reg_cancel_btn = gr.Button("取消", size="sm")

    # ── Forgot password panel ─────────────────────────────────────────
    with gr.Column(visible=False) as forgot_panel:
        gr.Markdown("### 重設密碼")
        forgot_email_in         = gr.Textbox(label="Email 地址", placeholder="your@email.com")
        forgot_check_btn        = gr.Button("查詢安全問題", variant="secondary")
        forgot_question_display = gr.Markdown("", visible=False)
        forgot_answer_in        = gr.Textbox(label="您的答案", visible=False)
        forgot_new_password_in  = gr.Textbox(label="新密碼", type="password", visible=False)
        forgot_reset_btn        = gr.Button("重設密碼", variant="primary", visible=False)
        forgot_msg              = gr.Markdown("")
        forgot_back_btn         = gr.Button("返回登入", size="sm")

    # ── Main chat area ────────────────────────────────────────────────
    with gr.Row():

        # ── Left: chat ────────────────────────────────────────────────
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                label="TransitFlow 助理",
                height=420,
                value=[WELCOME_MESSAGE],   # ✅ 顯示歡迎訊息
            )

            with gr.Row():
                msg = gr.Textbox(
                    placeholder="請輸入問題，例如：NR01 到 NR05 有哪些班次？",
                    show_label=False,
                    scale=4,
                )
                send_btn = gr.Button("送出", variant="primary", scale=1)

            with gr.Row():
                clear_btn    = gr.Button("🗑️ 清除對話", size="sm")
                debug_toggle = gr.Checkbox(label="🔍 顯示資料庫除錯面板", value=True)

            debug_panel = gr.Markdown(value="", visible=False)

        # ── Right: sidebar ────────────────────────────────────────────
        with gr.Column(scale=1):

            gr.Markdown("### 🤖 AI 模型")
            chat_model_dropdown = gr.Dropdown(
                choices=get_chat_model_choices(),
                value=get_initial_chat_model_value(),
                label="對話模型",
                info="Ollama 在本機執行，Gemini 需要 API key。",
            )
            provider_status = gr.Markdown(value="**使用中：** llama3.2:1b")
            ollama_status   = gr.Markdown(value=get_ollama_status())

            gr.Markdown("---")

            gr.Markdown("### 🚇 捷運快速選站")
            for label, prefix in METRO_STATIONS:
                gr.Button(label, size="sm").click(
                    fn=lambda p=prefix: p,
                    outputs=msg,
                )

            gr.Markdown("### 🚂 國鐵快速選站")
            for label, prefix in RAIL_STATIONS:
                gr.Button(label, size="sm").click(
                    fn=lambda p=prefix: p,
                    outputs=msg,
                )

            gr.Markdown("---")

            gr.Markdown("### 💡 常用問題範例")
            for example in EXAMPLES:
                gr.Button(example, size="sm").click(
                    fn=lambda e=example: e,
                    outputs=msg,
                )

    # ── Event wiring ──────────────────────────────────────────────────

    chat_model_dropdown.change(
        fn=on_chat_model_change,
        inputs=chat_model_dropdown,
        outputs=[provider_status, ollama_status],
    )

    send_btn.click(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    msg.submit(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    clear_btn.click(
        fn=clear_conversation,
        outputs=[chatbot, agent_history_state, debug_panel],
    )

    # Panel toggles
    login_btn.click(fn=show_login_panel, outputs=[login_panel, register_panel, forgot_panel])
    register_btn.click(fn=show_register_panel, outputs=[login_panel, register_panel, forgot_panel])
    login_cancel_btn.click(fn=hide_all_panels, outputs=[login_panel, register_panel, forgot_panel])
    reg_cancel_btn.click(fn=hide_all_panels, outputs=[login_panel, register_panel, forgot_panel])
    forgot_link_btn.click(fn=show_forgot_panel, outputs=[login_panel, register_panel, forgot_panel])
    forgot_back_btn.click(fn=show_login_panel, outputs=[login_panel, register_panel, forgot_panel])

    # Login
    login_submit_btn.click(
        fn=do_login,
        inputs=[login_email_in, login_password_in],
        outputs=[
            login_error_msg, current_user_state,
            login_btn, register_btn,
            user_info_display, logout_btn,
            login_panel,
        ],
    )

    # Logout
    logout_btn.click(
        fn=do_logout,
        outputs=[
            current_user_state,
            login_btn, register_btn,
            user_info_display, logout_btn,
            login_panel, register_panel, forgot_panel,
        ],
    )

    # Register
    reg_submit_btn.click(
        fn=do_register,
        inputs=[
            reg_email_in, reg_first_name_in, reg_surname_in,
            reg_year_in, reg_password_in, reg_question_in, reg_answer_in,
        ],
        outputs=[
            reg_error_msg, current_user_state,
            login_btn, register_btn,
            user_info_display, logout_btn,
            register_panel,
        ],
    )

    # Forgot password
    forgot_check_btn.click(
        fn=forgot_find_question,
        inputs=[forgot_email_in],
        outputs=[
            forgot_msg, forgot_question_display,
            forgot_answer_in, forgot_new_password_in, forgot_reset_btn,
        ],
    )

    forgot_reset_btn.click(
        fn=forgot_reset_password,
        inputs=[forgot_email_in, forgot_answer_in, forgot_new_password_in],
        outputs=[forgot_msg],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(),
    )

