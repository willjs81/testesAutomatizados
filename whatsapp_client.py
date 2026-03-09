"""
Cliente para enviar mensagens no WhatsApp e ler respostas do bot.
Usa Selenium - mantÃ©m sessÃ£o e exibe o fluxo completo no terminal.
Simula digitaÃ§Ã£o humana para evitar bloqueios.
"""
import random
import time

from config import (
    WHATSAPP_NUMERO_ALVO,
    DELAY_ENTRE_MENSAGENS,
    USE_WHATSAPP_PROFILE,
    DELAY_DIGITACAO_MS,
    DELAY_ANTES_DIGITAR,
    BLIP_KEY,
    BLIP_BOT_ID,
    WHATSAPP_MEU_NUMERO,
    BLIP_USER_ID,
    BLIP_USER_DOMAIN,
    USE_WHATSAPP,
)


class WhatsAppClient:
    """Cliente WhatsApp com Selenium - envia e lÃª respostas."""

    def __init__(self, numero: str = None):
        self.numero = numero or WHATSAPP_NUMERO_ALVO
        self.delay = DELAY_ENTRE_MENSAGENS
        self._driver = None

    def _get_driver(self):
        """Inicializa o Chrome (uma vez)."""
        if self._driver is None:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options
            from webdriver_manager.chrome import ChromeDriverManager

            options = Options()
            # Evita crash do Chrome no Windows
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--remote-debugging-port=0")
            # Perfil mantÃ©m sessÃ£o do WhatsApp Web (evita QR toda vez)
            if USE_WHATSAPP_PROFILE:
                import os
                profile_path = os.path.join(
                    os.path.expanduser("~"),
                    ".whatsapp_selenium_profile"
                )
                os.makedirs(profile_path, exist_ok=True)
                options.add_argument(f"--user-data-dir={profile_path}")

            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=options)
        return self._driver

    def _abrir_chat(self):
        """Abre o chat com o nÃºmero alvo."""
        driver = self._get_driver()
        numero = self.numero.replace(" ", "").replace("-", "").replace("+", "")
        url = f"https://web.whatsapp.com/send?phone={numero}"
        driver.get(url)
        time.sleep(3)

    def iniciar(self) -> bool:
        """
        Inicia o navegador e abre o chat.
        Retorna True se pronto. Na primeira vez, escaneie o QR code.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        self._get_driver()
        self._abrir_chat()

        # Aguarda campo de mensagem (indica que estÃ¡ no chat)
        try:
            wait = WebDriverWait(self._driver, 60)
            wait.until(EC.presence_of_element_located((
                By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab="10"]'
            )))
            return True
        except Exception:
            # Pode estar na tela do QR code
            return False

    def enviar_mensagem(self, texto: str) -> None:
        """Envia mensagem de texto."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import ElementClickInterceptedException
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver = self._get_driver()

        # Campo de digitaÃ§Ã£o
        selectors = [
            'div[contenteditable="true"][data-tab="10"]',
            'div[contenteditable="true"][data-tab="1"]',
            'div[contenteditable="true"]',
        ]
        input_box = None
        for sel in selectors:
            try:
                input_box = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                break
            except Exception:
                continue

        if not input_box:
            raise RuntimeError("Campo de mensagem nÃ£o encontrado")

        try:
            input_box.click()
        except ElementClickInterceptedException:
            # Modal/lista interativa aberto: fecha (ESC) e tenta clicar novamente.
            input_box.send_keys(Keys.ESCAPE)
            time.sleep(0.4)
            input_box = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab="10"]')
                )
            )
            input_box.click()
        time.sleep(0.3)

        # Simula digitaÃ§Ã£o humana (evita bloqueio do nÃºmero)
        time.sleep(DELAY_ANTES_DIGITAR)
        for char in texto:
            input_box.send_keys(char)
            # Delay variÃ¡vel por caractere (mais natural)
            ms = DELAY_DIGITACAO_MS + random.randint(-30, 50)
            ms = max(50, ms)
            time.sleep(ms / 1000.0)

        time.sleep(0.2)
        input_box.send_keys(Keys.ENTER)

    def _xpath_literal(self, text: str) -> str:
        """Escapa string para uso seguro em XPath."""
        if '"' not in text:
            return f'"{text}"'
        if "'" not in text:
            return f"'{text}'"
        parts = text.split('"')
        return 'concat(' + ', \'"\', '.join([f'"{p}"' for p in parts]) + ')'

    def _clicar_por_texto(self, texto: str, timeout: int = 20) -> bool:
        """
        Clica em elemento interativo do WhatsApp Web usando texto visível.
        Retorna True quando consegue clicar.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver = self._get_driver()
        t = self._xpath_literal(texto.strip())

        xpaths = [
            f"//button[.//span[contains(normalize-space(.), {t})] or contains(normalize-space(.), {t})]",
            f"//*[@role='button'][.//span[contains(normalize-space(.), {t})] or contains(normalize-space(.), {t})]",
            f"//span[contains(normalize-space(.), {t})]/ancestor::*[@role='button' or self::button][1]",
            f"//*[contains(@aria-label, {t}) and (@role='button' or self::button)]",
            f"//div[@role='button' and .//*[contains(normalize-space(.), {t})]]",
        ]

        for xp in xpaths:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.XPATH, xp))
                )
                elems = driver.find_elements(By.XPATH, xp)
                for el in reversed(elems):
                    try:
                        if not el.is_displayed():
                            continue
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                        try:
                            if el.is_enabled():
                                el.click()
                            else:
                                continue
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def clicar_opcao_por_texto(self, texto: str, timeout: int = 20) -> None:
        """
        Clica em opÃ§Ã£o/botÃ£o visÃ­vel no chat por texto.
        Exemplo: clicar_opcao_por_texto("Sim, confirmo")
        """
        ok = self._clicar_por_texto(texto, timeout=timeout)
        if not ok:
            raise RuntimeError(f"NÃ£o foi possÃ­vel clicar na opÃ§Ã£o '{texto}'.")

    def _clicar_primeiro_disponivel(self, xpaths: list, timeout: int = 5) -> bool:
        """Tenta clicar no primeiro elemento clicÃ¡vel entre vÃ¡rios XPaths."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver = self._get_driver()
        for xp in xpaths:
            try:
                el = WebDriverWait(driver, timeout).until(
                    EC.element_to_be_clickable((By.XPATH, xp))
                )
                el.click()
                return True
            except Exception:
                continue
        return False

    def selecionar_opcao_lista(self, titulo_lista: str, opcao: str, timeout: int = 30) -> None:
        """
        Seleciona uma opÃ§Ã£o em lista interativa do WhatsApp.
        Exemplo:
          selecionar_opcao_lista("Escolher ofertas", "Seguir sem proteÃ§Ã£o")
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver = self._get_driver()

        # 1) Abre a lista (botÃ£o no chat)
        abriu = self._clicar_por_texto(titulo_lista, timeout=timeout)
        if not abriu:
            raise RuntimeError(f"NÃ£o foi possÃ­vel abrir a lista '{titulo_lista}'.")

        # 2) Aguarda o modal/lista abrir e seleciona opÃ§Ã£o
        # WhatsApp pode renderizar a lista em diferentes containers; usa espera flexÃ­vel.
        try:
            t = self._xpath_literal(titulo_lista.strip())
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    f"//div[@role='dialog']//*[contains(normalize-space(.), {t})] | "
                    f"//*[contains(normalize-space(.), {t}) and "
                    f"(contains(@aria-label, 'lista') or contains(@class, 'copyable-text') or @role='dialog')]"
                ))
            )
        except Exception:
            # Segue para tentativa de clique; em alguns layouts o tÃ­tulo nÃ£o aparece no DOM.
            pass

        opt = self._xpath_literal(opcao.strip())
        op_xpaths = [
            f"//div[@role='dialog']//span[contains(normalize-space(.), {opt})]/ancestor::*[@role='button' or self::button][1]",
            f"//div[@role='dialog']//*[@role='button'][.//span[contains(normalize-space(.), {opt})] or contains(normalize-space(.), {opt})]",
            f"//div[@role='dialog']//span[contains(normalize-space(.), {opt})]",
            f"//*[contains(normalize-space(.), {opt}) and (self::span or self::div)]",
        ]

        clicou_opcao = False
        for xp in op_xpaths:
            try:
                # Primeiro tenta forma padrÃ£o clicÃ¡vel.
                el = WebDriverWait(driver, 4).until(
                    EC.presence_of_element_located((By.XPATH, xp))
                )
                try:
                    WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.XPATH, xp)))
                    el.click()
                except Exception:
                    # Fallback: sobe para ancestral clicÃ¡vel e usa JS click.
                    alvo = el
                    try:
                        anc = el.find_element(
                            By.XPATH,
                            "./ancestor::*[@role='button' or self::button or @tabindex][1]"
                        )
                        alvo = anc
                    except Exception:
                        pass
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", alvo)
                    driver.execute_script("arguments[0].click();", alvo)
                clicou_opcao = True
                break
            except Exception:
                continue
        if not clicou_opcao:
            raise RuntimeError(f"NÃ£o foi possÃ­vel selecionar a opÃ§Ã£o '{opcao}'.")

        # 3) Em alguns layouts Ã© necessÃ¡rio confirmar/envio da seleÃ§Ã£o
        #    Tentativa silenciosa; se nÃ£o houver botÃ£o, segue sem erro.
        self._clicar_primeiro_disponivel(
            [
                "//div[@role='dialog']//button[@aria-label='Enviar']",
                "//div[@role='dialog']//*[@data-icon='send']",
                "//div[@role='dialog']//*[@role='button'][.//span[contains(normalize-space(.), 'Enviar')]]",
                "//div[@role='dialog']//*[@role='button'][.//span[contains(normalize-space(.), 'Confirmar')]]",
            ],
            timeout=4,
        )

        # Pequena folga para propagaÃ§Ã£o da interaÃ§Ã£o no chat
        time.sleep(1.5)

    def _ler_resposta_via_blip(self) -> str:
        """ObtÃ©m Ãºltima resposta do bot via API Blip (mais confiÃ¡vel que DOM do WhatsApp)."""
        if not (BLIP_KEY and BLIP_BOT_ID):
            return ""
        for tentativa in range(3):
            try:
                from blip_client import BlipClient
                user_id = WHATSAPP_MEU_NUMERO if (USE_WHATSAPP and WHATSAPP_MEU_NUMERO) else BLIP_USER_ID
                if not user_id:
                    return ""
                blip = BlipClient()
                blip.user_identity = user_id.replace("+", "").replace(" ", "")
                if "@" not in blip.user_identity:
                    blip.user_identity = f"{blip.user_identity}@{BLIP_USER_DOMAIN or 'wa.gw.msging.net'}"
                items = blip._obter_items_thread()
                resp = blip._extrair_ultima_resposta(items)
                if resp and "[resposta do bot nÃ£o encontrada]" not in resp and "[sem mensagens" not in resp:
                    return resp
                if tentativa < 2:
                    time.sleep(2)  # Aguarda API sincronizar
            except Exception:
                if tentativa < 2:
                    time.sleep(2)
        return ""

    def _ler_ultima_resposta_selenium(self) -> str:
        """Tenta ler a Ãºltima mensagem via DOM do WhatsApp Web."""
        from selenium.webdriver.common.by import By

        driver = self._driver
        if not driver:
            return ""

        selectors_tentativas = [
            ('div[data-id$="false"] span.selectable-text', True),   # incoming
            ('span.selectable-text[dir="ltr"]', False),            # todas
            ('div[data-id] span[dir="ltr"]', False),
            ('[data-testid="msg-container"] span.selectable-text', False),
        ]
        for _ in range(3):
            time.sleep(1)
            for seletor, so_incoming in selectors_tentativas:
                try:
                    elems = driver.find_elements(By.CSS_SELECTOR, seletor)
                    if elems:
                        return elems[-1].text.strip() or ""
                except Exception:
                    pass
        return ""

    def _ler_ultima_resposta(self) -> str:
        """LÃª a Ãºltima resposta do bot. Prioriza API Blip (confiÃ¡vel), fallback para DOM."""
        # 1. API Blip - fonte mais confiÃ¡vel quando configurada
        resp = self._ler_resposta_via_blip()
        if resp:
            return resp
        # 2. Selenium/DOM - fallback
        resp = self._ler_ultima_resposta_selenium()
        if resp:
            return resp
        return "[resposta nÃ£o capturada]"

    def enviar_e_aguardar(self, texto: str, delay: float = None, exibir_resposta: bool = True) -> str:
        """
        Envia mensagem, aguarda e retorna a resposta do bot.
        Se exibir_resposta=True, imprime no terminal.
        """
        self.enviar_mensagem(texto)
        aguardar = delay if delay is not None else self.delay
        time.sleep(aguardar)

        resposta = self._ler_ultima_resposta()
        if exibir_resposta and resposta:
            if "[resposta nÃ£o capturada]" in resposta:
                print("    â† Bot: [resposta nÃ£o capturada - verifique BLIP_KEY/BOT_ID para captura via API]")
            else:
                prefixo = "    â† Bot: "
                for ln in resposta.replace("\r", "").split("\n"):
                    while len(ln) > 120:
                        print(f"{prefixo}{ln[:120]}")
                        ln, prefixo = ln[120:], " " * 11
                    print(f"{prefixo}{ln}")
                    prefixo = " " * 11
        return resposta

    def fechar(self):
        """Fecha o navegador."""
        if self._driver:
            self._driver.quit()
            self._driver = None

