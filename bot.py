import os
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

from sheets_client import SheetsClient
from validator import check_email_validity

load_dotenv()

WAITING_FOR_CONFIRMATION = 1

def fetch_sheet_data_sync():
    sheets = SheetsClient()
    return sheets.get_sheet_data()

def highlight_emails_sync(invalid_emails_map):
    sheets = SheetsClient()
    sheets.highlight_invalid_emails(invalid_emails_map)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Send /email_validator to start validating CRM emails.")

async def validate_emails_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Starting CRM email validation. This might take a few minutes if the sheet is large...")
    
    try:
        # Offload the blocking Google Sheets fetch to a background thread
        data, client_col_idx, email_col_idx = await asyncio.to_thread(fetch_sheet_data_sync)
    except Exception as e:
        await update.message.reply_text(f"Error accessing Google Sheets: {str(e)}\n\nPlease ensure your .env has SPREADSHEET_NAME and WORKSHEET_NAME set properly, and the service account has access.")
        return ConversationHandler.END
        
    # Collect all emails to check
    emails_to_check = []
    for i, row in enumerate(data):
        row_idx = i + 1 
        
        if len(row) <= max(client_col_idx, email_col_idx):
            continue
            
        client_id = row[client_col_idx]
        email_cell = row[email_col_idx]
        
        if not email_cell.strip():
            continue
            
        emails = [e.strip() for e in email_cell.split(',') if e.strip()]
        for em in emails:
            emails_to_check.append((row_idx, client_id, em))
            
    await update.message.reply_text(f"Found {len(emails_to_check)} emails to check. Scanning concurrently... (this should only take a few seconds!)")
    
    # Process concurrently with a semaphore to prevent overloading
    sem = asyncio.Semaphore(25) # Max 25 concurrent connections
    
    async def check_single(row_idx, client_id, em):
        async with sem:
            is_valid, reason = await asyncio.to_thread(check_email_validity, em)
            if not is_valid:
                return (row_idx, client_id, em, reason)
            return None
            
    tasks = [check_single(*item) for item in emails_to_check]
    results = await asyncio.gather(*tasks)
    
    invalid_emails_map = {} 
    report_lines = []
    
    for res in results:
        if res:
            row_idx, client_id, em, reason = res
            report_lines.append(f"Client ID: {client_id} | Email: {em} | Reason: {reason}")
            if row_idx not in invalid_emails_map:
                invalid_emails_map[row_idx] = []
            invalid_emails_map[row_idx].append(em)
            
    if not report_lines:
        await update.message.reply_text("All emails in the CRM look healthy!")
        return ConversationHandler.END
        
    context.user_data['invalid_emails_map'] = invalid_emails_map
    
    report_text = "Found the following unreachable/invalid emails:\n\n" + "\n".join(report_lines)
    
    # Telegram has a 4096 character limit per message
    for i in range(0, len(report_text), 4000):
        await update.message.reply_text(report_text[i:i+4000])
        
    await update.message.reply_text(
        "Do you want me to highlight these invalid emails in red in the Google Sheet? (Yes/No)"
    )
    return WAITING_FOR_CONFIRMATION

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if 'yes' in text:
        await update.message.reply_text("Highlighting invalid emails in Google Sheets...")
        try:
            # Offload the blocking highlight operation to a background thread
            await asyncio.to_thread(highlight_emails_sync, context.user_data['invalid_emails_map'])
            await update.message.reply_text("Done! Invalid emails have been highlighted.")
        except Exception as e:
            await update.message.reply_text(f"Error highlighting: {str(e)}")
    else:
        await update.message.reply_text("Okay, I will not highlight the emails.")
        
    context.user_data.clear()
    return ConversationHandler.END
    
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        return
        
    application = Application.builder().token(token).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("email_validator", validate_emails_command)],
        states={
            WAITING_FOR_CONFIRMATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirmation)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    
    print("Bot is running. Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
