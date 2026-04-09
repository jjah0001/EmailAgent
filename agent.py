import anthropic
import base64
import json
import time
from gmail_auth import get_gmail_service

client = anthropic.Anthropic(api_key="YOUR_ANTHROPIC_API_KEY")
service = get_gmail_service()

LABELS = ["Work", "Personal", "Finance", "Spam", "Newsletter", "Urgent"]

SYSTEM_PROMPT = """You are an email sorting assistant. For each email you receive, respond ONLY with a JSON object in this exact format:
{
  "category": "<one of: Work, Personal, Finance, Spam, Newsletter, Urgent>",
  "urgent": <true or false>,
  "summary": "<one sentence summary>",
  "draft_reply": "<a polite, professional reply draft, or null if no reply is needed>"
}
Do not include any other text outside the JSON."""


def get_unread_emails(max_results=10):
    result = service.users().messages().list(
        userId='me', labelIds=['INBOX'], q='is:unread', maxResults=max_results
    ).execute()
    return result.get('messages', [])


def get_email_content(msg_id):
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = {h['name']: h['value'] for h in msg['payload']['headers']}
    subject = headers.get('Subject', '(no subject)')
    sender = headers.get('From', 'Unknown')

    # Extract body
    body = ''
    parts = msg['payload'].get('parts', [])
    if parts:
        for part in parts:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                break
    else:
        data = msg['payload']['body'].get('data', '')
        body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

    return msg_id, subject, sender, body[:3000]  # cap at 3000 chars


def ask_claude(subject, sender, body):
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"From: {sender}\nSubject: {subject}\n\nBody:\n{body}"
        }]
    )
    return json.loads(response.content[0].text)


def apply_label(msg_id, label_name):
    # Get or create label
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    label_id = next((l['id'] for l in labels if l['name'] == label_name), None)

    if not label_id:
        new_label = service.users().labels().create(
            userId='me', body={'name': label_name}
        ).execute()
        label_id = new_label['id']

    service.users().messages().modify(
        userId='me', id=msg_id,
        body={'addLabelIds': [label_id], 'removeLabelIds': ['UNREAD']}
    ).execute()


def create_draft_reply(msg_id, reply_text):
    # Get original message for threading
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = {h['name']: h['value'] for h in msg['payload']['headers']}
    thread_id = msg['threadId']
    to = headers.get('From', '')
    subject = headers.get('Subject', '')

    raw_msg = f"To: {to}\nSubject: Re: {subject}\n\n{reply_text}"
    encoded = base64.urlsafe_b64encode(raw_msg.encode()).decode()

    service.users().drafts().create(
        userId='me',
        body={'message': {'raw': encoded, 'threadId': thread_id}}
    ).execute()


def flag_urgent(msg_id):
    service.users().messages().modify(
        userId='me', id=msg_id,
        body={'addLabelIds': ['STARRED', 'UNREAD']}
    ).execute()


def process_emails():
    print("Checking for unread emails...")
    emails = get_unread_emails()

    if not emails:
        print("No new emails.")
        return

    for email in emails:
        msg_id, subject, sender, body = get_email_content(email['id'])
        print(f"Processing: {subject[:60]}")

        try:
            result = ask_claude(subject, sender, body)

            apply_label(msg_id, result['category'])

            if result['urgent']:
                flag_urgent(msg_id)
                print(f"  → URGENT: {result['summary']}")

            if result['draft_reply']:
                create_draft_reply(msg_id, result['draft_reply'])
                print(f"  → Draft reply created")

            print(f"  → Categorised as: {result['category']}")

        except Exception as e:
            print(f"  → Error processing email: {e}")


if __name__ == '__main__':
    while True:
        process_emails()
        time.sleep(300)  # poll every 5 minutes