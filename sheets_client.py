import os
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

class SheetsClient:
    def __init__(self):
        self.service_account_file = os.getenv('SERVICE_ACCOUNT_FILE', 'service_account.json')
        
        self.credentials = Credentials.from_service_account_file(
            self.service_account_file, scopes=SCOPES)
        self.gc = gspread.authorize(self.credentials)
        self.service = build('sheets', 'v4', credentials=self.credentials)
        
        self.spreadsheet_name = os.getenv('SPREADSHEET_NAME')
        self.worksheet_name = os.getenv('WORKSHEET_NAME', 'Sheet1')
        self.spreadsheet_id = None
        
    def get_sheet_data(self):
        if not self.spreadsheet_name:
            raise ValueError("SPREADSHEET_NAME is not set in .env")
            
        # We can find the spreadsheet by name
        sh = self.gc.open(self.spreadsheet_name)
        self.spreadsheet_id = sh.id
        worksheet = sh.worksheet(self.worksheet_name)
        
        values = worksheet.get_all_values()
        if not values:
            return [], -1, -1
            
        headers = [h.strip().lower() for h in values[0]]
        
        # Dynamically find the email column
        email_col_idx = -1
        for i, h in enumerate(headers):
            if 'email' in h:
                email_col_idx = i
                break
                
        # Dynamically find a client identifier column (ID, Name, Lead, etc.)
        client_col_idx = -1
        for i, h in enumerate(headers):
            if 'id' in h or 'name' in h or 'client' in h or 'lead' in h or 'business' in h:
                client_col_idx = i
                break
                
        if email_col_idx == -1:
            raise ValueError("Could not find any column header containing 'email'. Please ensure the sheet has an email column.")
            
        if client_col_idx == -1:
            client_col_idx = 0 # Default to the very first column if we can't find an ID/Name
            
        data = values[1:] # Skip headers
        return data, client_col_idx, email_col_idx
        
    def process_invalid_emails(self, invalid_emails_map, action_map):
        """
        invalid_emails_map: dict of { row_idx: [list of invalid email strings] }
        action_map: dict of { row_idx: 'delete' | 'highlight' }
        row_idx is 0-indexed relative to the worksheet (0 is header, 1 is first data row).
        """
        if not self.spreadsheet_name:
            return
            
        sh = self.gc.open(self.spreadsheet_name)
        self.spreadsheet_id = sh.id
        worksheet = sh.worksheet(self.worksheet_name)
        sheet_id = worksheet.id
        
        values = worksheet.get_all_values()
        if not values:
            return
            
        headers = [h.strip().lower() for h in values[0]]
        email_col_idx = -1
        for i, h in enumerate(headers):
            if 'email' in h:
                email_col_idx = i
                break
                
        if email_col_idx == -1:
            return
            
        requests = []
        for row_idx, bad_emails in invalid_emails_map.items():
            if row_idx >= len(values):
                continue
                
            action = action_map.get(row_idx, 'highlight')
            cell_value = values[row_idx][email_col_idx]
            
            if action == 'delete':
                # Remove the bad emails from the comma-separated string
                parts = [p.strip() for p in cell_value.split(',')]
                new_parts = [p for p in parts if p and p not in bad_emails]
                new_value = ', '.join(new_parts)
                
                requests.append({
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_idx,
                            "endRowIndex": row_idx + 1,
                            "startColumnIndex": email_col_idx,
                            "endColumnIndex": email_col_idx + 1
                        },
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": new_value},
                                        "textFormatRuns": [] # Clear rich text formatting
                                    }
                                ]
                            }
                        ],
                        "fields": "userEnteredValue,textFormatRuns"
                    }
                })
            else:
                # Highlight action
                format_runs = []
                parts = [p.strip() for p in cell_value.split(',')]
                search_start = 0
                
                for part in parts:
                    if not part:
                        continue
                    start_pos = cell_value.find(part, search_start)
                    if start_pos == -1:
                        continue
                        
                    end_pos = start_pos + len(part)
                    
                    if part in bad_emails:
                        format_runs.append({
                            "startIndex": start_pos,
                            "format": {
                                "foregroundColorStyle": {
                                    "rgbColor": {"red": 1.0, "green": 0.0, "blue": 0.0}
                                },
                                "bold": True
                            }
                        })
                        if end_pos < len(cell_value):
                             format_runs.append({
                                 "startIndex": end_pos,
                                 "format": {}
                             })
                             
                    search_start = end_pos
                    
                if format_runs:
                    if format_runs[0].get('startIndex', 0) != 0:
                        format_runs.insert(0, {
                            "startIndex": 0,
                            "format": {}
                        })
                        
                    requests.append({
                        "updateCells": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": row_idx,
                                "endRowIndex": row_idx + 1,
                                "startColumnIndex": email_col_idx,
                                "endColumnIndex": email_col_idx + 1
                            },
                            "rows": [
                                {
                                    "values": [
                                        {
                                            "userEnteredValue": {"stringValue": cell_value},
                                            "textFormatRuns": format_runs
                                        }
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue,textFormatRuns"
                        }
                    })
                
        if requests:
            # Batch update the formatting and values
            body = {'requests': requests}
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body=body
            ).execute()
