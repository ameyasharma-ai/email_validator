import os
import asyncio
import warnings
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler

# Suppress the harmless CallbackQueryHandler warning
warnings.filterwarnings("ignore", message="If 'per_message=False', 'CallbackQueryHandler' will not be tracked for every message.*")

from sheets_client import SheetsClient
from validator import check_email_validity

load_dotenv()

WAITING_FOR_CONFIRMATION = 1

def fetch_sheet_data_sync():
    sheets = SheetsClient()
    return sheets.get_sheet_data()

def process_emails_sync(invalid_emails_map, action_map):
    sheets = SheetsClient()
    sheets.process_invalid_emails(invalid_emails_map, action_map)

def apply_corrections_sync(corrected_emails_map):
    sheets = SheetsClient()
    sheets.apply_corrections(corrected_emails_map)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Send /email_validator to start validating CRM emails, or /commands to see all options.")

async def commands_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Available Commands*\n\n"
        "/email\\_validator - Scans your Google Sheet using full API + DNS validation.\n"
        "/email\\_validator --local - Scans using ONLY local DNS (very fast, bypasses API).\n"
        "/commands - Shows this list of commands.\n"
        "/cancel - Cancels any ongoing operation.\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def validate_emails_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    use_api = True
    if context.args and '--local' in context.args:
        use_api = False
        await update.message.reply_text("Starting local CRM email validation (API bypassed). This will be very fast!")
    else:
        await update.message.reply_text("Starting full CRM email validation. This might take a few minutes if the sheet is large...")
    
    try:
        data, client_col_idx, email_col_idx = await asyncio.to_thread(fetch_sheet_data_sync)
    except Exception as e:
        await update.message.reply_text(f"Error accessing Google Sheets: {str(e)}\n\nPlease ensure your .env has SPREADSHEET_NAME and WORKSHEET_NAME set properly, and the service account has access.")
        return ConversationHandler.END
        
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
    
    sem = asyncio.Semaphore(25) 
    
    async def check_single(row_idx, client_id, em):
        async with sem:
            is_valid, reason, corrected_email = await asyncio.to_thread(check_email_validity, em, use_api)
            return (row_idx, client_id, em, is_valid, reason, corrected_email)
            
    tasks = [check_single(*item) for item in emails_to_check]
    results = await asyncio.gather(*tasks)
    
    invalid_emails_map = {} 
    row_to_client_id = {}
    report_lines = []
    
    corrected_emails_map = {}
    num_corrections = 0
    
    for res in results:
        if res:
            row_idx, client_id, em, is_valid, reason, corrected_email = res
            
            if corrected_email:
                if row_idx not in corrected_emails_map:
                    corrected_emails_map[row_idx] = {}
                corrected_emails_map[row_idx][em] = corrected_email
                num_corrections += 1
                
            if not is_valid:
                final_email = corrected_email if corrected_email else em
                report_lines.append(f"Client ID: {client_id} | Email: {final_email} | Reason: {reason}")
                if row_idx not in invalid_emails_map:
                    invalid_emails_map[row_idx] = []
                invalid_emails_map[row_idx].append(final_email)
                row_to_client_id[row_idx] = client_id
                
    if num_corrections > 0:
        await update.message.reply_text(f"✨ Found and auto-corrected {num_corrections} typos! Updating Google Sheets now...")
        await asyncio.to_thread(apply_corrections_sync, corrected_emails_map)
            
    if not report_lines:
        await update.message.reply_text("All emails in the CRM look healthy!")
        return ConversationHandler.END
        
    context.user_data['invalid_emails_map'] = invalid_emails_map
    context.user_data['row_to_client_id'] = row_to_client_id
    
    report_text = "Found the following unreachable/invalid emails:\n\n" + "\n".join(report_lines)
    
    for i in range(0, len(report_text), 4000):
        await update.message.reply_text(report_text[i:i+4000])
        
    keyboard = [
        [
            InlineKeyboardButton("Highlight All", callback_data="highlight all"),
            InlineKeyboardButton("Delete All", callback_data="delete all")
        ],
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    prompt_text = (
        "How do you want to handle these invalid emails?\n"
        "Click a button below, OR reply with text to specify IDs:\n"
        "- `delete all except ID1, ID2` (highlights exceptions)\n"
        "- `delete ID1, ID2` (highlights the rest)"
    )
    await update.message.reply_text(prompt_text, reply_markup=reply_markup, parse_mode="Markdown")
    return WAITING_FOR_CONFIRMATION

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = query.data
    
    if text == 'cancel':
        await query.edit_message_text("Operation cancelled.")
        context.user_data.clear()
        return ConversationHandler.END
        
    invalid_emails_map = context.user_data.get('invalid_emails_map', {})
    action_map = {}
    
    if text == 'highlight all':
        for row_idx in invalid_emails_map:
            action_map[row_idx] = 'highlight'
    elif text == 'delete all':
        for row_idx in invalid_emails_map:
            action_map[row_idx] = 'delete'
            
    await query.edit_message_text("Processing your request in Google Sheets...")
    try:
        await asyncio.to_thread(process_emails_sync, invalid_emails_map, action_map)
        await query.edit_message_text("Done! The Google Sheet has been updated.")
    except Exception as e:
        await query.edit_message_text(f"Error updating sheet: {str(e)}")
        
    context.user_data.clear()
    return ConversationHandler.END

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    
    if text == 'cancel':
        await update.message.reply_text("Operation cancelled.")
        context.user_data.clear()
        return ConversationHandler.END
        
    invalid_emails_map = context.user_data.get('invalid_emails_map', {})
    row_to_client_id = context.user_data.get('row_to_client_id', {})
    
    action_map = {}
    
    try:
        if text == 'highlight all':
            for row_idx in invalid_emails_map:
                action_map[row_idx] = 'highlight'
        elif text == 'delete all':
            for row_idx in invalid_emails_map:
                action_map[row_idx] = 'delete'
        elif text.startswith('delete all except'):
            ids_str = text.replace('delete all except', '').strip()
            exception_ids = [i.strip() for i in ids_str.split(',') if i.strip()]
            for row_idx in invalid_emails_map:
                client_id = row_to_client_id.get(row_idx, "").strip().lower()
                if client_id in exception_ids:
                    action_map[row_idx] = 'highlight'
                else:
                    action_map[row_idx] = 'delete'
        elif text.startswith('delete'):
            ids_str = text.replace('delete', '').strip()
            delete_ids = [i.strip() for i in ids_str.split(',') if i.strip()]
            for row_idx in invalid_emails_map:
                client_id = row_to_client_id.get(row_idx, "").strip().lower()
                if client_id in delete_ids:
                    action_map[row_idx] = 'delete'
                else:
                    action_map[row_idx] = 'highlight'
        else:
            await update.message.reply_text("I didn't understand that command. Please use one of the formats provided (e.g. `highlight all`, `delete all`, `cancel`).")
            return WAITING_FOR_CONFIRMATION
            
        await update.message.reply_text("Processing your request in Google Sheets...")
        await asyncio.to_thread(process_emails_sync, invalid_emails_map, action_map)
        await update.message.reply_text("Done! The Google Sheet has been updated.")
        
    except Exception as e:
        await update.message.reply_text(f"Error updating sheet: {str(e)}")
        
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
            WAITING_FOR_CONFIRMATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirmation),
                CallbackQueryHandler(handle_button_click)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("commands", commands_list))
    application.add_handler(conv_handler)
    
    print("Bot is running. Press Ctrl+C to stop.")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        print("\nBot stopped gracefully.")
    except Exception as e:
        print(f"\nBot stopped with an error: {e}")

if __name__ == "__main__":
    main()
