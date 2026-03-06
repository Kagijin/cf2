import customtkinter as ctk
import time
import threading
import keyboard
import os
import sys 
import random
import ctypes
import cv2
import numpy as np
import mss # Substitui o pyautogui para capturas ultrarrápidas

cv2.setUseOptimized(True)
cv2.setNumThreads(max(1, min(4, os.cpu_count() or 1)))

# --- CONFIGURAÇÕES DE SISTEMA ---
def set_high_priority():
    try:
        pid = os.getpid()
        # 0x00000080 representa HIGH_PRIORITY_CLASS no Windows
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00000080)
    except: pass

# Códigos hexadecimais (DirectInput) para as teclas simuladas
DIK_1, DIK_3 = 0x02, 0x04 
DIK_A, DIK_S, DIK_D = 0x1E, 0x1F, 0x20
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004

# --- ESTRUTURAS DO CTYPES (USER-MODE) ---
# Nota arquitetural: Estas estruturas mapeiam a API do Windows (User32.dll).
# Apesar de serem de baixo nível (C/C++), elas operam em Ring 3 (User-Mode), 
# enviando eventos para a fila de mensagens do sistema operacional.
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong), 
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong), 
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", ctypes.c_byte * 8)]
    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]

def SendInput(input_structure):
    ctypes.windll.user32.SendInput(1, ctypes.pointer(input_structure), ctypes.sizeof(input_structure))

def resource_path(relative_path):
    try: base_path = sys._MEIPASS
    except Exception: base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def PressKey(hexKeyCode):
    ii_ = INPUT(type=1, ki=KEYBDINPUT(0, hexKeyCode, 0x0008, 0, None))
    SendInput(ii_)

def ReleaseKey(hexKeyCode):
    ii_ = INPUT(type=1, ki=KEYBDINPUT(0, hexKeyCode, 0x0008 | 0x0002, 0, None))
    SendInput(ii_)

def KernelClickFast():
    extra = ctypes.c_ulong(0)
    ii_down = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, ctypes.pointer(extra)))
    ii_up = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, ctypes.pointer(extra)))
    SendInput(ii_down)
    SendInput(ii_up)

# --- MOTOR PRINCIPAL ---
class AnkaBotFarm(ctk.CTk):
    def __init__(self):
        super().__init__()
        set_high_priority() 
        self.title("DazzBOT") # Atualizado para V2 :)
        self.geometry("450x550")
        self.resizable(False, False)
        
        self.rodando = False 
        self.app_alive = True
        
        # --- DICIONÁRIO DE COOLDOWNS ---
        # Armazena o timestamp do último clique de cada botão para evitar spam
        self.cooldowns = {}
        self.scale_factor = 0.70  # Reduz custo do matchTemplate em VMs mais fracas
        self.loop_sleep = 0.0015
        self.entrar_roi_rel = (0.62, 0.62, 0.38, 0.38)  # Região onde aparece "Entrar na partida"
        self.botoes_lobby_direita = ["cancelartotal", "cancelar", "entrar"]
        self.min_match_confirmacoes = {"entrar": 2}
        self.match_streak = {}
        
        # --- CACHE DE TEMPLATES (OPEN-CV) ---
        self.templates = {}
        self.prioridades = [
            "confirmartotal", "cancelartotal", "okupo", "verificardps", 
            "vertrofeu", "cancelar2", "confirmar3", "dps1", "ok2", "ok3", "ok4", "trofeu1", "xzao"
        ]
        
        # Carrega todas as imagens para a memória RAM (escala de cinza) na inicialização
        for nome in (["entrar", "cancelar", "convite", "banner"] + self.prioridades):
            ext = ".jpeg" if any(x in nome for x in ["cancelar2", "confirmar3", "dps1", "ok2", "ok3", "ok4", "trofeu1", "xzao"]) else ".png"
            caminho = resource_path(f"{nome}{ext}")
            if os.path.exists(caminho):
                img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    # Mantém versão original e reduzida para reduzir uso de CPU em VMs
                    if self.scale_factor < 1.0:
                        w = max(1, int(img.shape[1] * self.scale_factor))
                        h = max(1, int(img.shape[0] * self.scale_factor))
                        img_reduzida = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                    else:
                        img_reduzida = img

                    self.templates[nome] = {
                        "full": img,
                        "scaled": img_reduzida,
                        "shape_full": img.shape,
                        "shape_scaled": img_reduzida.shape,
                    }
                    self.cooldowns[nome] = 0.0 # Inicializa o cooldown zerado

        self.var_farm = ctk.BooleanVar(value=True)
        self.var_move_tipo = ctk.StringVar(value="off")
        self.var_troca_arma = ctk.BooleanVar(value=False)

        self.montar_interface()
        # Inicia a thread de monitoramento de tela independentemente da UI
        threading.Thread(target=self.monitoramento_lobby, daemon=True).start()

    def montar_interface(self):
        try:
            bg_label = ctk.CTkLabel(self, image=ctk.CTkImage(self.img_data["banner"], size=(450, 550)), text="")
            bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            bg_label.lower() 
        except: self.configure(fg_color="#121212")

        self.lbl_status = ctk.CTkLabel(self, text="Status: AGUARDANDO...", font=("Arial", 18, "bold"), text_color="orange", fg_color="#1a1a1a")
        self.lbl_status.pack(pady=(20, 10), padx=20, fill="x")

        f_farm = ctk.CTkFrame(self, border_width=1, border_color="#00FF7F", fg_color="transparent")
        f_farm.pack(pady=10, padx=20, fill="x")
        ctk.CTkCheckBox(f_farm, text="MODO FARM ATIVO", variable=self.var_farm, font=("Arial", 13, "bold"), text_color="#00FF7F").pack(pady=10)

        f_mov = ctk.CTkFrame(self, border_width=1, border_color="#555", fg_color="#1a1a1a")
        f_mov.pack(pady=10, padx=20, fill="x")
        ctk.CTkLabel(f_mov, text="MOVIMENTO", font=("Arial", 11, "bold")).pack(pady=5)
        ctk.CTkRadioButton(f_mov, text="OFF", variable=self.var_move_tipo, value="off").pack(side="left", padx=15, pady=5)
        ctk.CTkRadioButton(f_mov, text="Segurar A", variable=self.var_move_tipo, value="A").pack(side="left", padx=15, pady=5)
        ctk.CTkRadioButton(f_mov, text="S + D (Bomba)", variable=self.var_move_tipo, value="BOMBA").pack(side="left", padx=15, pady=5)
        
        ctk.CTkCheckBox(self, text="Troca de Arma (3-1)", variable=self.var_troca_arma, bg_color="#1a1a1a").pack(pady=10)

        self.btn_start = ctk.CTkButton(self, text="INICIAR BOT (F5)", fg_color="#228B22", height=50, font=("Arial", 16, "bold"), command=self.iniciar_thread)
        self.btn_start.pack(pady=(20, 5), padx=20, fill="x")
        self.btn_stop = ctk.CTkButton(self, text="PARAR BOT (F6)", fg_color="#B22222", height=45, font=("Arial", 14, "bold"), command=self.parar_bot)
        self.btn_stop.pack(pady=5, padx=20, fill="x")

        keyboard.add_hotkey('f5', self.iniciar_thread)
        keyboard.add_hotkey('f6', self.parar_bot)

    def registrar_match(self, template_name, encontrou):
        atual = self.match_streak.get(template_name, 0)
        if encontrou:
            atual += 1
        else:
            atual = 0
        self.match_streak[template_name] = atual
        return atual

    def pode_clicar_agora(self, template_name, streak_atual):
        minimo = self.min_match_confirmacoes.get(template_name, 1)
        return streak_atual >= minimo

    def buscar_e_clicar(self, screenshot, template_name, threshold=0.8, cooldown_segundos=0.8,
                       roi=None, usar_escala=True, monitor_offset=(0, 0)):
        # 1. Verifica se a imagem ainda está em tempo de recarga (cooldown)
        if template_name in self.cooldowns:
            tempo_atual = time.time()
            if tempo_atual - self.cooldowns[template_name] < cooldown_segundos:
                return False # Sai da função sem fazer nada, pois ainda está no cooldown

        template_data = self.templates.get(template_name)
        if template_data is None:
            return False

        if usar_escala and self.scale_factor < 1.0:
            template = template_data["scaled"]
            t_h, t_w = template_data["shape_scaled"]
            escala = self.scale_factor
        else:
            template = template_data["full"]
            t_h, t_w = template_data["shape_full"]
            escala = 1.0

        area = screenshot
        roi_x, roi_y = 0, 0
        if roi is not None:
            x, y, w, h = roi
            roi_x = max(0, x)
            roi_y = max(0, y)
            roi_w = min(w, screenshot.shape[1] - roi_x)
            roi_h = min(h, screenshot.shape[0] - roi_y)
            if roi_w <= 0 or roi_h <= 0:
                return False
            area = screenshot[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]

        if area.shape[0] < t_h or area.shape[1] < t_w:
            return False

        # 2. Faz a matemática matricial para achar a imagem
        res = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        # 3. Se a confiança for maior que o threshold configurado
        encontrou = max_val >= threshold
        streak = self.registrar_match(template_name, encontrou)
        if encontrou and self.pode_clicar_agora(template_name, streak):
            x_inicial = roi_x + max_loc[0]
            y_inicial = roi_y + max_loc[1]

            # 4. Sorteia o clique dentro da área da imagem
            tx_frame = random.randint(x_inicial + 3, max(x_inicial + t_w - 3, x_inicial + 3))
            ty_frame = random.randint(y_inicial + 3, max(y_inicial + t_h - 3, y_inicial + 3))

            # 5. Converte coordenadas do frame para monitor real
            tx_monitor = int(tx_frame / escala)
            ty_monitor = int(ty_frame / escala)
            tx = monitor_offset[0] + tx_monitor
            ty = monitor_offset[1] + ty_monitor

            ctypes.windll.user32.SetCursorPos(tx, ty)
            KernelClickFast()

            # 6. Registra o timestamp atual para acionar o cooldown deste botão específico
            self.cooldowns[template_name] = time.time()
            self.match_streak[template_name] = 0
            return True

        return False

    def monitoramento_lobby(self):
        # Instancia o MSS. Fica aberto enquanto o app viver para máxima performance.
        with mss.mss() as sct:
            # Seleciona o monitor principal (geralmente o 1. Mude para 2 se usar tela estendida e o jogo estiver lá)
            monitor = sct.monitors[1] 

            while self.app_alive:
                if not self.rodando:
                    time.sleep(0.1)
                    continue

                # --- PASSO 1: Captura ultrarrápida da memória da GPU/Sistema ---
                img_bruta = np.asarray(sct.grab(monitor), dtype=np.uint8)
                # O MSS retorna BGRA, o OpenCV precisa de BGR ou Cinza. Convertendo direto para cinza:
                screen_gray = cv2.cvtColor(img_bruta, cv2.COLOR_BGRA2GRAY)
                screen_gray_scaled = screen_gray
                if self.scale_factor < 1.0:
                    novo_w = max(1, int(screen_gray.shape[1] * self.scale_factor))
                    novo_h = max(1, int(screen_gray.shape[0] * self.scale_factor))
                    screen_gray_scaled = cv2.resize(screen_gray, (novo_w, novo_h), interpolation=cv2.INTER_AREA)

                monitor_offset = (monitor.get("left", 0), monitor.get("top", 0))

                # --- PASSO 2: Limpeza Total de Popups ---
                limpou = False
                for btn in self.prioridades:
                    # Passando 1.5s de cooldown para dar tempo do popup sumir da tela após clicar
                    if self.buscar_e_clicar(screen_gray_scaled, btn, threshold=0.8, cooldown_segundos=0.3, monitor_offset=monitor_offset):
                        limpou = True
                        break 
                
                if limpou: 
                    time.sleep(0.05)
                    continue

                # --- PASSO 3: Botões da direita (Cancelar/Entrar) ---
                # Todos aparecem no mesmo local do botão "Iniciar" no lobby.
                h_full, w_full = screen_gray.shape[:2]
                rx, ry, rw, rh = self.entrar_roi_rel
                entrar_roi = (int(w_full * rx), int(h_full * ry), int(w_full * rw), int(h_full * rh))

                clicou_lobby_direita = False
                for botao in self.botoes_lobby_direita:
                    # "Cancelar" e "Entrar" são textos pequenos: buscar em resolução cheia aumenta precisão.
                    threshold = 0.78 if "cancelar" in botao else 0.90
                    if self.buscar_e_clicar(
                        screen_gray,
                        botao,
                        threshold=threshold,
                        cooldown_segundos=0.3,
                        roi=entrar_roi,
                        usar_escala=False,
                        monitor_offset=monitor_offset,
                    ):
                        clicou_lobby_direita = True
                        break

                if clicou_lobby_direita:
                    time.sleep(0.05)
                    continue

                # --- PASSO 4: Convite ---
                if self.var_farm.get() and random.random() < 0.1:
                    self.buscar_e_clicar(screen_gray_scaled, "convite", threshold=0.8, cooldown_segundos=0.9, monitor_offset=monitor_offset)

                # Alívio crucial para a CPU da Máquina Virtual não bater 100%
                time.sleep(self.loop_sleep)

    def iniciar_thread(self):
        if not self.rodando:
            self.rodando = True
            self.lbl_status.configure(text="Status: EM OPERAÇÃO", text_color="#00FF7F")
            threading.Thread(target=self.motor_movimento, daemon=True).start()

    def parar_bot(self):
        self.rodando = False
        self.lbl_status.configure(text="Status: PARADO", text_color="orange")
        ReleaseKey(DIK_A); ReleaseKey(DIK_S); ReleaseKey(DIK_D)

    def motor_movimento(self):
        u_troca = time.time()
        while self.rodando:
            m = self.var_move_tipo.get()
            if m == "A": PressKey(DIK_A)
            elif m == "BOMBA": PressKey(DIK_S); PressKey(DIK_D) # Corrigido de DID_D para DIK_D
            else: ReleaseKey(DIK_A); ReleaseKey(DIK_S); ReleaseKey(DIK_D)
            
            # Troca de arma 3-1 simples
            if self.var_troca_arma.get() and (time.time() - u_troca > 10):
                PressKey(DIK_3); time.sleep(0.05); ReleaseKey(DIK_3)
                time.sleep(0.1); PressKey(DIK_1); time.sleep(0.05); ReleaseKey(DIK_1)
                u_troca = time.time()
            time.sleep(0.05)

if __name__ == "__main__":
    app = AnkaBotFarm()
    app.mainloop()
