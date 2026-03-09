# Automação de Testes de Chatbot - Blip

Executa fluxos de teste e **exibe o retorno da API Blip no terminal**.

## Modos

1. **Blip API** (recomendado): envia via API, obtém resposta via API – sem navegador
2. **WhatsApp**: envia via WhatsApp Web – usa Chrome

## Configuração

1. Instale: `pip install -r requirements.txt`

2. Copie `.env.example` para `.env` e configure a **Blip API**:
   ```
   BLIP_KEY=Key xxx...        # Do portal Blip (Configurações > Chave)
   BLIP_BOT_ID=labclinicadev  # Identificador do bot
   BLIP_USER_ID=5511999999999 # Número para simular o usuário
   DELAY_ENTRE_MENSAGENS=3
   ```

## Executando

```bash
python run_flow.py
```

Exemplo do que aparece no terminal:

```
  → Enviado: oi
  ← Blip API: Olá! 👋 Seja bem-vindo(a). Este é o canal...

  → Enviado: Agendar consulta
  ← Blip API: Para sua segurança, preciso confirmar sua identidade...
```

Com Blip API: sem navegador, retorno direto da API.

## Fluxos

Edite ou crie arquivos em `flows/`:

```json
{
  "nome": "Fluxo feliz - Agendar consulta",
  "delay_padrao": 3,
  "passos": [
    {"mensagem": "oi", "delay": 3},
    {"mensagem": "Agendar consulta", "delay": 4},
    {"mensagem": "006.394.547-12", "delay": 3}
  ]
}
```

Para outro fluxo:
```bash
python run_flow.py flows/outro_fluxo.json
```

## Dados de teste (dados_teste.json)

Dados fixos (CEP, endereço, banco, agência) e **array de CPFs** para rodar o fluxo:

```json
{
  "cpfs": ["537.055.723-34", "006.394.547-12"],
  "cep": "01022-000",
  "numero_casa": "5698",
  "banco": "104 Caixa Econômica",
  "agencia": "1234"
}
```

No fluxo, use placeholders: `{{cpf}}`, `{{cep}}`, `{{numero}}`, `{{banco}}`, `{{agencia}}`.

Para usar um CPF específico do array:
```bash
python run_flow.py flows/fluxo_refinanciamento.json 0   # usa cpfs[0]
python run_flow.py flows/fluxo_refinanciamento.json 1   # usa cpfs[1]
```

## Cenários por tipo de teste

Cada cenário usa um CPF do array (cpf_index) e um fluxo específico:

| Cenário | Descrição |
|---------|-----------|
| fluxo_feliz | Aceita oferta e conclui até dados bancários |
| aceite_oferta | Confirma a primeira oferta |
| recusa_oferta | Escolhe "Agora não" |
| outro_valor | Edição de valores - "Outro valor" |
| input_inesperado | Mensagem fora do contexto |
| inatividade | Abandono no meio do fluxo |
| fluxo_infeliz | CPF inválido |

```bash
# Listar cenários
python run_cenarios.py

# Executar por ID
python run_cenarios.py fluxo_feliz
python run_cenarios.py recusa_oferta

# Executar por número
python run_cenarios.py 0
```
