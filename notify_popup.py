"""
notify_popup.py — Windows notification popup for DMCPatchAgent.

Spawned as a subprocess by agent.py. Result communicated via stdout:
    confirmed
    delay:<minutes>
    do_restart
    dismissed
"""
import sys
import json
import tkinter as tk
import winsound


def play_alert(urgency):
    sounds = {
        'critical': winsound.MB_ICONHAND,
        'warning':  winsound.MB_ICONEXCLAMATION,
        'info':     winsound.MB_ICONASTERISK,
    }
    try:
        winsound.MessageBeep(sounds.get(urgency, winsound.MB_ICONEXCLAMATION))
    except Exception:
        pass


def run_popup(payload):
    urgency       = payload.get('urgency', 'warning')
    title         = payload.get('title', 'System Notice')
    message       = payload.get('message', '')
    deliver_as    = payload.get('deliver_as', 'notify')
    restart_at    = payload.get('restart_at')
    minutes_left  = payload.get('minutes_left')
    delay_options = payload.get('delay_options', [15, 60, 240, 1440])
    delays_used   = payload.get('delays_used', 0)
    max_delays    = payload.get('max_delays', 4)

    delays_remaining = max(0, max_delays - delays_used)
    is_final         = deliver_as == 'do_restart' or (minutes_left is not None and minutes_left <= 5)
    is_restart       = deliver_as in ('do_restart', 'remind') and restart_at is not None

    palette = {
        'critical': {'bg': '#1a0505', 'header': '#c0392b', 'border': '#e74c3c', 'btn': '#c0392b'},
        'warning':  {'bg': '#1a1205', 'header': '#d68910', 'border': '#f39c12', 'btn': '#d68910'},
        'info':     {'bg': '#051a1a', 'header': '#1a7a8a', 'border': '#17a2b8', 'btn': '#1a7a8a'},
    }
    colors = palette.get(urgency, palette['warning'])

    result = [None]       # use list so inner functions can write to it
    delay_result = [None]

    # ── Build window ──────────────────────────────────────────────────────────
    root = tk.Tk()
    root.title(title)
    root.configure(bg=colors['bg'])
    root.resizable(False, False)
    root.attributes('-topmost', True)
    root.protocol('WM_DELETE_WINDOW', lambda: None)  # disable X button

    # Centre on screen
    w, h = 560, 440
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f'{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}')

    # ── Header ────────────────────────────────────────────────────────────────
    icons = {'critical': '(!)', 'warning': '/!\\', 'info': '(i)'}
    header = tk.Frame(root, bg=colors['header'], height=56)
    header.pack(fill='x')
    header.pack_propagate(False)

    tk.Label(header, text=icons.get(urgency, '/!\\'),
             font=('Consolas', 18, 'bold'),
             bg=colors['header'], fg='white').pack(side='left', padx=14)

    tk.Label(header, text=title.upper(),
             font=('Segoe UI', 13, 'bold'),
             bg=colors['header'], fg='white').pack(side='left', padx=4)

    # ── Accent line ───────────────────────────────────────────────────────────
    tk.Frame(root, bg=colors['border'], height=3).pack(fill='x')

    # ── Message ───────────────────────────────────────────────────────────────
    msg_frame = tk.Frame(root, bg=colors['bg'], padx=24, pady=16)
    msg_frame.pack(fill='both', expand=True)

    tk.Label(msg_frame, text=message,
             font=('Segoe UI', 11),
             bg=colors['bg'], fg='#e8e8e8',
             wraplength=500, justify='left').pack(anchor='w')

    # ── Countdown ─────────────────────────────────────────────────────────────
    if is_restart and minutes_left is not None:
        countdown_var = tk.StringVar()
        countdown_frame = tk.Frame(root, bg=colors['bg'], padx=24)
        countdown_frame.pack(fill='x')
        tk.Label(countdown_frame,
                 textvariable=countdown_var,
                 font=('Consolas', 14, 'bold'),
                 bg=colors['bg'],
                 fg=colors['border']).pack(anchor='w', pady=4)

        remaining = [int(minutes_left * 60)]

        def update_countdown():
            if remaining[0] <= 0:
                countdown_var.set('Restarting now...')
                root.after(3000, lambda: finish('do_restart'))
                return
            m, s = divmod(remaining[0], 60)
            h2, m2 = divmod(m, 60)
            if h2:
                countdown_var.set(f'Restart in {h2}h {m2:02d}m {s:02d}s')
            else:
                countdown_var.set(f'Restart in {m2}m {s:02d}s')
            remaining[0] -= 1
            root.after(1000, update_countdown)

        update_countdown()

    # ── Separator ─────────────────────────────────────────────────────────────
    tk.Frame(root, bg=colors['border'], height=1).pack(fill='x', padx=20, pady=8)

    # ── Buttons ───────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(root, bg=colors['bg'], padx=20, pady=12)
    btn_frame.pack(fill='x')

    def finish(res, delay_mins=None):
        result[0] = res
        delay_result[0] = delay_mins
        root.quit()
        root.destroy()

    def make_btn(parent, text, cmd, primary=False):
        bg       = colors['btn'] if primary else '#2a2a2a'
        hover_bg = colors['border'] if primary else '#3a3a3a'
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg='white', relief='flat',
                      font=('Segoe UI', 10, 'bold' if primary else 'normal'),
                      padx=14, pady=7, cursor='hand2',
                      activebackground=hover_bg, activeforeground='white',
                      bd=0)
        return b

    if deliver_as == 'do_restart':
        make_btn(btn_frame, 'Restart Now',
                 lambda: finish('do_restart'), primary=True).pack(side='left', padx=4)
    else:
        confirm_text = 'Acknowledged' if not is_restart else 'Understood'
        make_btn(btn_frame, confirm_text,
                 lambda: finish('confirmed'), primary=True).pack(side='left', padx=4)

    if not is_final and delays_remaining > 0 and delay_options:
        tk.Label(btn_frame, text='Remind me in:',
                 font=('Segoe UI', 9),
                 bg=colors['bg'], fg='#888888').pack(side='left', padx=(16, 4))

        def fmt(mins):
            if mins < 60:   return f'{mins}m'
            if mins < 1440: return f'{mins // 60}h'
            return f'{mins // 1440}d'

        for mins in delay_options:
            make_btn(btn_frame, fmt(mins),
                     lambda m=mins: finish('delay', m)).pack(side='left', padx=2)

    # ── Footer ────────────────────────────────────────────────────────────────
    if delays_remaining <= 0 and not is_final:
        tk.Label(root, text='No more delays available.',
                 font=('Segoe UI', 9, 'italic'),
                 bg=colors['bg'], fg='#888888').pack(pady=(0, 10))
    elif is_restart and restart_at:
        tk.Label(root, text=f'Scheduled restart: {restart_at}',
                 font=('Consolas', 9),
                 bg=colors['bg'], fg='#555555').pack(pady=(0, 10))

    play_alert(urgency)
    root.mainloop()

    return result[0], delay_result[0]



def write_result(result_str, result_file=None):
    # When launched via CreateProcessAsUser stdout cannot cross session boundary
    # so the result is written to a temp file instead.
    if result_file:
        try:
            with open(result_file, 'w') as f:
                f.write(result_str)
        except Exception as e:
            print(f'Failed to write result file: {e}', file=sys.stderr)
    else:
        print(result_str)


if __name__ == '__main__':
    args = sys.argv[1:]

    # Extract --result-file <path> if present
    result_file = None
    if '--result-file' in args:
        idx = args.index('--result-file')
        if idx + 1 < len(args):
            result_file = args[idx + 1]
            args = args[:idx] + args[idx + 2:]

    # Log file next to exe for crash diagnostics
    import os, traceback
    exe_dir  = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
    log_file = os.path.join(exe_dir, 'notify_popup.log')

    def popup_log(msg):
        try:
            with open(log_file, 'a', encoding='utf-8') as lf:
                import datetime
                lf.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
        except Exception:
            pass

    try:
        popup_log(f"Started. args={args} result_file={result_file}")

        if not args:
            popup_log("ERROR: no payload argument")
            write_result('dismissed', result_file)
            sys.exit(1)

        try:
            payload = json.loads(args[0])
            popup_log(f"Payload parsed: title={payload.get('title')} urgency={payload.get('urgency')}")
        except json.JSONDecodeError as e:
            popup_log(f"ERROR: bad json: {e}")
            write_result('dismissed', result_file)
            sys.exit(1)

        val, delay_mins = run_popup(payload)
        popup_log(f"Popup result: val={val} delay_mins={delay_mins}")

        if val == 'confirmed':
            write_result('confirmed', result_file)
        elif val == 'delay' and delay_mins:
            write_result(f'delay:{delay_mins}', result_file)
        elif val == 'do_restart':
            write_result('do_restart', result_file)
        else:
            write_result('dismissed', result_file)

    except Exception as e:
        tb = traceback.format_exc()
        popup_log(f"CRASH: {e}\n{tb}")
        write_result('dismissed', result_file)
        sys.exit(1)