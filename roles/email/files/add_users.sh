#!/bin/sh
#
# Path to the user list
USER_FILE="/users.txt"
 
if [ ! -f "$USER_FILE" ]; then
    echo "Error:$USER_FILE not found."
    exit 1
fi
 
while IFS= read -r line || [ -n "$line" ]; do
  #Strip any Windows carriage returns (\r)
  clean_line=$(echo "$line" | tr -d '\r')
 
  #Skip empty lines
  if [ -z "$clean_line" ]; then
      continue
  fi
 
  #Extract username (everything before the colon)
  user=$(echo "$clean_line" | cut -d: -f1)
 
  #Check if the line actually contained a colon/user
  if [ "$user" = "$clean_line" ]; then
  echo "Skipping malformed line: $clean_line"
  continue
  fi
 
  echo "Processing user: $user"
 
  #Create the user if they don't already exist
  if id "$user" >/dev/null 2>&1; then
      echo " - User already exists."
  else
      adduser --disabled-password --gecos "" --allow-all-names "$user"
  fi
 
  #Set the password
  echo "$clean_line" | chpasswd
  if [ $? -eq 0 ]; then
      echo " - Password set successfully."
  else
      echo " - FAILED to set password."
  fi
 
done < "$USER_FILE"
 
echo "User import process complete."
