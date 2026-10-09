Verification wizard (frontend/verify)

Files: verify.js, verify.css, icons.js, demo.html. No build step.

API
  SltVerify.open({language, prefill:{name, phone}, backendBase,
                  onVerified({display_name, language}), onCancel()})
  SltVerify.close()
  backendBase defaults to window.backendHttpBase() if present, else "".

i18n keys used (SltI18n.t(key, vars)); variables passed:
  verify.step1.*            title, name_label, name_placeholder, phone_label,
                            phone_placeholder, terms_prefix, terms_link, and,
                            privacy_link, back, next
  verify.step2.sms_option / email_option   {masked}
  verify.step2.title, send_otp, change_number, no_contact
  verify.step3.subtitle     {masked}
  verify.step3.digit_aria   {n, index, total}
  verify.step3.countdown    {time (mm:ss), seconds}
  verify.step3.title, verify, resend, expired
  verify.errors.rate_limited {seconds, count}   cooldown {seconds, count}
  verify.errors.attempts_left {count, attempts}
  verify.errors.invalid_details (422 invalid_details), generic (fallback), phone_format, invalid, expired, locked, unavailable, network
  Without SltI18n the key itself is shown.

Manual checklist (open demo.html?stub=1 through any static server, or
?backend=http://127.0.0.1:8000 for the real backend)
  1. Open the wizard. Focus is in the Name field. Next is disabled.
  2. Type a name, a number such as 011 234 5678 (also +94112345678), tick the
     box. Next enables only when all three hold. Terms and Privacy links open
     in new tabs.
  3. Next with name "nobody": one generic error, stays on step 1.
  4. Next with a number ending 0000: no_contact message and a change link.
  5. Normal details: step 2 lists SMS and email with masked values. Send OTP
     (rocket icon). "Change connection number" returns to step 1, fields kept.
  6. Step 3: focus is in the first box. Typing advances, Backspace on an empty
     box goes back, pasting 123456 fills all six, arrow keys move.
  7. A wrong code shows "attempts left"; the third wrong code locks and shows
     Resend OTP. Code 123456 closes the wizard and the page shows the name.
  8. Add &expires=10: the visible timer counts down, inputs and Verify disable
     at 0:00, Resend is enabled after 30 s from sending.
  9. Escape: step 3 goes to step 2, step 2 to step 1, step 1 cancels.
 10. Tab and Shift+Tab never leave the dialog. Focus rings are visible.
 11. Toggle dark theme, resize to 360 px wide: nothing scrolls sideways.
 12. Screen reader: countdown is announced only at 1:00, 0:30, 0:10 and 0:00.
 13. DevTools: Application tab shows nothing stored; Console shows no codes
     or numbers.
