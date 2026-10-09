# Wizard browser run: English, Sinhala, Tamil

Prerequisites and server start are in README.txt in this folder. Run the
whole script three times, once per language L in {en, si, ta}, each time
from a fresh incognito window.

## Test data (fake rows from `python -m verification.seed_demo`)

| Case | Name to type | Phone to type | Contacts |
|---|---|---|---|
| A both contacts | Test Customer | 011 200 0001 | SMS `07X XXX X123`, email with an all X local part, domain example.invalid |
| B SMS only | Test Sinhala Customer | 011 200 0002 | SMS `07X XXX X456`, no email option |
| C email only | Test Email Only | 011 200 0003 | email only, no SMS option |
| X unknown | Anyone | 011 299 9999 | none |

Reading the table: the SMS mask always has the shape `07X XXX X` plus the
last three digits. The email mask has only `X` characters before the `@`
and the domain stays readable. No full mobile number and no full email
address may appear on screen, in Network responses, or in the Console.

Codes: the six digit code is printed in the SERVER window. For negative
steps use any other six digits.

## Setup per language

S1. Open http://127.0.0.1:8000 in a fresh incognito window.
S2. Open DevTools, tabs Console and Network (preserve log on).
S3. Pick language L on the start screen.
    Expect: the page text switches and `document.documentElement.lang`
    equals L (run it in the console).
    si and ta: glyphs render with no empty boxes. An empty box means Noto
    Sans Sinhala or Tamil did not load: FAIL.
S4. Confirm you are a guest: no name shown, no verified badge.

## Block 1: a guest is held back from customer service

1.1 Open the text chat. Send the question for L:
    * en: What is my current bill balance and when was my last payment?
    * si: මගේ වත්මන් බිල්පත් ශේෂය කීයද, අවසන් ගෙවීම කවදාද?
    * ta: எனது தற்போதைய பில் நிலுவை எவ்வளவு, கடைசி கட்டணம் எப்போது?

    Expect: a reply in language L, in L's own script, that names 1212 and
    points to verification. It arrives at once (no model delay). In the
    Network tab the /chat response `mode` is not a model mode name.
1.2 Ask a public question: "Tell me about SLT fibre broadband packages"
    (translated for si and ta). Expect a normal answer, not the 1212 notice.

## Block 2: step 1, details

2.1 Choose "Existing user". Expect a modal dialog, dimmed background, focus
    inside it. Console: `document.activeElement` is inside the dialog; the
    dialog has role "dialog" and aria-modal "true".
2.2 Expect the title, a Name field, an SLT telephone number field, the
    checkbox "I agree to the Terms of Use and Privacy Policy", Back and
    Next, all in language L. Compare with frontend/i18n/L.json keys
    verify.step1.*.
2.3 Expect Next disabled while a field is empty or the box is unticked.
2.4 Click Terms of Use. Expect a new tab on an English slt.lk page (the si
    and ta pages do not exist, by design). Close it and return.
2.5 Negative: name "Anyone", phone 011 299 9999 (case X), tick the box,
    Next. Expect ONE generic error (verify.errors.invalid_details), you
    stay on step 1, the fields are kept.
2.6 Negative: name "Wrong Name", phone 011 200 0001 (right number, wrong
    name). Expect EXACTLY the same message as 2.5. In Network compare the two
    /verify/lookup responses: same status 422, same JSON body. Any
    difference is a FAIL.
2.7 Negative: phone "abc". Expect the phone format message
    (verify.errors.phone_format), still on step 1.
2.8 Type case A with odd spacing and case, for example name
    "  test   CUSTOMER " and phone "+94 11 200 0001". Tick the box, Next.
    Expect step 2.

## Block 3: step 2, choose where to send

3.1 Expect one option per contact on file. Case A: SMS with `07X XXX X123`
    and an email option with an all X local part. Case B: only SMS. Case C:
    only email.
3.2 Scan `document.body.innerText` and the /verify/lookup response in
    Network: no full mobile number, no full email.
3.3 Click "Use a different number". Expect step 1 with name and phone still
    filled in. Press Next again.
3.4 Select SMS and click Send code. Expect step 3, and a line with a six
    digit code in the server window. Network: POST /verify/otp/send is 200
    with expires_in 120, resend_in 30 and a masked value, and no code or
    unmasked contact in the body.

## Block 4: step 3, code entry

4.1 Expect the subtitle with the same masked value, six separate digit
    boxes, a countdown starting near 2:00 that falls each second, a Verify
    button and a "Send a new code" button. The first box has focus. The
    resend button is disabled for the first 30 seconds.
4.2 Keyboard: type three digits (focus advances each time), press Backspace
    on an empty box (focus goes back), use the arrow keys (focus moves).
    Clear all boxes.
4.3 Paste: copy the code from the server window and paste into the first
    box. Expect all six boxes filled. Do not press Verify yet.
4.4 Wrong code: replace it with wrong digits and click Verify. Expect
    verify.errors.invalid and "2 attempts left", and focus back on the first
    box. Network: 401, code "invalid", attempts_left 2.
4.5 Wrong again: expect "1 attempts left" in language L.
4.6 Wrong a third time: expect the locked message (verify.errors.locked) or
    zero attempts left, and Verify disabled. Then enter the CORRECT code:
    still refused with the locked message (Network 423). A locked challenge
    never works.
4.7 Wait until 30 seconds after the first send, then click "Send a new
    code". Expect a new line with a NEW code in the server window. Before
    the 30 seconds the button must be disabled; if you force the request
    from the console it answers 429 code "cooldown" with retry_after of at
    most 30.
4.8 Old code is dead: enter the code from the first send. Expect it refused
    (invalid or expired), never accepted.
4.9 Expiry: wait for the countdown to reach 0:00 (about 2 minutes after the
    latest send). Expect the digit boxes and Verify disabled and
    verify.step3.expired shown. Send a new code to continue. A forced
    verify after expiry answers 410 code "expired".
4.10 Escape: on step 3 it goes to step 2, then step 1, then closes the
     dialog. Reopen and go through to step 3 again with a fresh code.
4.11 Focus trap: Tab and Shift+Tab repeatedly. Focus never leaves the
     dialog. Focus rings are visible in light and dark theme.

## Block 5: success and the unlock

5.1 Enter the correct latest code and click Verify. Expect the dialog to
    close, a greeting with the customer's display name
    (verify.success.greeting in L), and the app in its verified state.
    Network: POST /verify/otp/verify is 200 with display_name and language,
    and a Set-Cookie for user_session with HttpOnly. In the console,
    `document.cookie` must NOT list user_session. Application tab: Local
    Storage and Session Storage hold no code, number or name from the
    wizard.
5.2 Console: no line contains a code, a phone number or an email.
5.3 Ask the SAME question as 1.1. Expect a real answer from the assistant
    (not the 1212 notice), addressed to the verified name.
5.4 Reload the page. Expect the verified state to persist (GET
    /verify/session returns verified true).
5.5 Log out in the app. Expect guest state, and the question from 1.1 is
    held back with the 1212 notice again.
5.6 Open a second incognito window: it is a guest (nothing shared).

## Block 6: layout and theme

6.1 Dark theme: toggle with the wizard open; colours follow the theme.
6.2 Resize to 360 px wide: no sideways scrolling, buttons reachable, the
    six boxes fit on one row.
6.3 Zoom to 200 percent: still usable.
6.4 si and ta: long labels wrap, nothing is cut off, line height keeps the
    script readable.
6.5 Reopen the wizard after switching language: all wizard text follows L.
    Only names, 1212 and the terms links may stay English.

## Block 7: accessibility spot checks

7.1 Error messages and the countdown sit in aria-live regions (read_page
    shows role alert or aria-live polite).
7.2 The digit boxes are labelled like "Digit 3 of 6" (verify.step3.digit_aria).
7.3 The code boxes use autocomplete one-time-code and a numeric keyboard
    (inputmode numeric).

## Result table (copy per language)

| Step | en | si | ta | Evidence |
|---|---|---|---|---|
| S1 to S4 setup, language, fonts | | | | |
| 1.1 guest held back, 1212, own script | | | | |
| 1.2 public question answered | | | | |
| 2.1 to 2.4 dialog, fields, links | | | | |
| 2.5 unknown number generic error | | | | |
| 2.6 wrong name identical to unknown | | | | |
| 2.7 phone format | | | | |
| 2.8 name and phone normalisation | | | | |
| 3.1 to 3.2 masks, no unmasked contact | | | | |
| 3.3 change number keeps fields | | | | |
| 3.4 send, 120 and 30 in response | | | | |
| 4.1 to 4.3 boxes, countdown, paste | | | | |
| 4.4 to 4.6 attempts and lock | | | | |
| 4.7 to 4.8 resend, old code dead | | | | |
| 4.9 expiry at 0:00 | | | | |
| 4.10 to 4.11 Escape, focus trap | | | | |
| 5.1 to 5.2 success, cookie HttpOnly, no leaks | | | | |
| 5.3 same question now answered | | | | |
| 5.4 to 5.6 reload, logout, second window | | | | |
| 6.x layout, dark, 360 px, zoom | | | | |
| 7.x accessibility | | | | |
