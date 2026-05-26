# Telegram CRM Email Validator

A blazing-fast, highly concurrent Telegram Bot designed to securely connect to a Google Sheet CRM, scrub it for bouncing and invalid emails, and allow you to purge or highlight them with a single tap from your phone.

## 🚀 Key Features

*   **Hybrid Validation Strategy:** Integrates with the AbstractAPI for professional verification (catching bounces, role emails, etc.) while automatically falling back to a strictly local, ISP-resistant DNS MX-record check if the API rate-limits or goes down.
*   **Insanely Fast & Highly Concurrent:** Uses `asyncio` threads and a semaphore system to concurrently scan 25 emails at the same time without blocking the Telegram bot's main loop.
*   **In-Memory DNS Caching:** Intelligently caches domain DNS checks in memory. If a sheet has 50 emails from the same company, the network is queried exactly *once*, completely preventing UDP packet drops or ISP rate-limiting.
*   **Smart Google Sheets Integration:** Does not blindly delete rows or cells! If a single cell contains `good_email@domain.com, bad_email@fake.com`, the bot is smart enough to use string manipulation to *only* delete the bad email, keeping your good data fully intact.
*   **Telegram Inline UI:** Provides instant 1-tap buttons right inside the Telegram chat to bulk-delete or bulk-highlight invalid emails. Also supports advanced natural language parsing for granular control (e.g., replying with `delete all except ID10, ID25`).

---

## 🛠️ Setup & Installation

### 1. Prerequisites
You must have Python 3.8+ installed. You also need three credentials:
1.  **Telegram Bot Token:** Create a bot via [@BotFather](https://t.me/botfather) on Telegram and get your token.
2.  **Google Service Account JSON:** Create a project in Google Cloud Console, enable the **Google Sheets API**, generate a Service Account, download its `.json` key, and share your specific Google Sheet with the generated service account email (giving it Editor access).
3.  **AbstractAPI Key (Optional but recommended):** Get a free API key from [AbstractAPI Email Validation](https://www.abstractapi.com/api/email-verification-validation-api) to detect bouncing/catch-all emails.

### 2. Environment Variables
Create a `.env` file in the root of the project with the following structure:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
EMAIL_API_KEY=your_abstractapi_key_here

SPREADSHEET_NAME=Google Maps Leads
WORKSHEET_NAME=Sheet1
SERVICE_ACCOUNT_FILE=service_account.json
```
*Note: Make sure your `service_account.json` file is placed in the same folder as the script!*

### 3. Installation
Open your terminal and install all required dependencies:
```bash
# Optional: Create a virtual environment
python -m venv venv
# Windows: venv\Scripts\activate
# Mac/Linux: source venv/bin/activate

pip install -r requirements.txt
```

### 4. Running the Bot
```bash
python bot.py
```
*The bot will log `Bot is running. Press Ctrl+C to stop.`*

---

## 🤖 Using the Bot

Start a chat with your Telegram Bot and use the following commands:

*   `/email_validator` - Executes a full scan. It uses the API (queued at 1 request/second to respect free-tier rate limits) and falls back to local DNS caching.
*   `/email_validator --local` - Completely bypasses the API and performs a raw DNS MX-record check. This is **extremely fast** (takes mere seconds for hundreds of emails) but might miss emails that bounce on valid domains.
*   `/commands` - Shows the help menu.
*   `/cancel` - Aborts any operation that is currently waiting for your input.

### Handling Invalid Emails
After the bot finishes scanning, it will list the invalid emails along with their Client IDs and Reasons. It will present you with interactive buttons:
*   **[ Highlight All ]**: Highlights the specific bad emails red inside the Google Sheet without deleting them.
*   **[ Delete All ]**: Purges all the bad emails from the sheet.

**Granular Control:**
If you want to keep some and delete others, ignore the buttons and type directly into the chat:
*   `delete all except 10, 15` (Deletes everything found, except Client IDs 10 and 15 which are highlighted instead).
*   `delete 10, 15` (Deletes only 10 and 15, and highlights everything else found).
