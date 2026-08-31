with open('execution/mt5_execution.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Fix line 770 (index 769) - has too many spaces
for i, line in enumerate(lines):
    if 'bid=_bid_retry' in line and line != '                    bid=_bid_retry,\n':
        print(f"Before: Line {i+1}: {repr(line[:60])}")
        lines[i] = '                    bid=_bid_retry,\n'
        print(f"After: Line {i+1}: {repr(lines[i][:60])}")
    if 'ask=_ask_retry,' in line and line.strip() == 'ask=_ask_retry,':
        # Check if it has wrong indentation
        if not line.startswith('                    '):
            print(f"Before: Line {i+1}: {repr(line[:60])}")
            lines[i] = '                    ask=_ask_retry,\n'
            print(f"After: Line {i+1}: {repr(lines[i][:60])}")

with open('execution/mt5_execution.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print("Done")
