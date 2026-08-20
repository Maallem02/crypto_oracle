# Run this in PowerShell (not inside venv needed) to find every copy of
# crypto_oracle.db on your machine, with size and last-modified time, so we
# can identify which one is actually still being written to.

Get-ChildItem -Path C:\Users\MSI -Recurse -Filter "crypto_oracle*.db" -ErrorAction SilentlyContinue |
    Select-Object FullName, Length, LastWriteTime |
    Sort-Object LastWriteTime -Descending |
    Format-Table -AutoSize
