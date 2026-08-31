import re

with open('execution/mt5_execution.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Fix 1: Attempt 2 - retry mt5_result is None block - fix bid/ask indentation
# The mangled version has too many spaces on bid line
old1 = """                    bid=_bid_retry,
                    ask=_ask_retry,
                    broker_ok=False,
                    retcode=-1,"""
# Check what's actually in the file
if old1 in content:
    print("Attempt 2 (None) block already correct")
else:
    # Try to find the mangled version
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'bid=_bid_retry' in line and 'ask=_ask_retry' not in line:
            print(f"Line {i+1}: {repr(line[:80])}")

# Fix 2: Attempt 2 - normal persist block - change bid=_bid to bid=_bid_retry
old2 = "                bid=_bid,\n                ask=_ask,\n                broker_ok=(int(mt5_result.retcode)"
new2 = "                bid=_bid_retry,\n                ask=_ask_retry,\n                broker_ok=(int(mt5_result.retcode)"
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("Fixed attempt 2 normal persist block")
else:
    print("Checking attempt 2 normal block...")
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'bid=_bid' in line and 'ask=_ask' in lines[i+1] if i+1 < len(lines) else False:
            print(f"Line {i+1}: {repr(line[:80])}")
            print(f"Line {i+2}: {repr(lines[i+1][:80])}")

# Fix 3: Replace vars() usage
old3 = "retry_reason=retry_reason if 'retry_reason' in vars() else None"
new3 = "retry_reason=retry_reason"
if old3 in content:
    content = content.replace(old3, new3, 1)
    print("Fixed vars() usage")
else:
    print("vars() usage not found or already fixed")

with open('execution/mt5_execution.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
