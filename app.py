"""This was designed to work in WINDOWS"""
import tkinter as tk
from tkinter import ttk
import subprocess
import time
import threading


class Root(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MultiPinger")
        # Using this ping loop as a condition to know when to stop all the pings.
        self.ping_loop = False
        # This list keeps track of all generated frames - used in the for loop for pinging each ip.
        self.list_of_generated_frames = []

        # Instantiate Initial Frames
        controlFrame = ControlFrame(self).grid(row=0, column=0)
        fGen = frameGeneratorFrame(self).grid(row=1, column=0)


class ControlFrame(ttk.Frame):
    def __init__(self, container):
        super().__init__(container)

        start_button = ttk.Button(self, text="Start", command=self.start)
        start_button.grid(row=0, column=0)
        stop_button = ttk.Button(self, text="Stop", command=self.stop)
        stop_button.grid(row=0, column=1)

    def start(self):
        print("starting pings")
        root.ping_loop = True
        x = threading.Thread(target=self.start_threadig_looping)
        x.start()

    def stop(self):
        print("stopping pings")
        root.ping_loop = False
        x = threading.Thread(target=self.clear_status_on_stop)
        x.start()

    def start_threadig_looping(self):
        while root.ping_loop == True:
            for each in root.list_of_generated_frames:
                each.start_ping()

    def clear_status_on_stop(self):
        # Reset status label BG to normal color.
        # Wait 5 seconds for all threads to stop.
        time.sleep(5)
        for each in root.list_of_generated_frames:
            each.status_label.config(bg="SystemButtonFace")


class ipFrame(ttk.Frame):
    def __init__(self, container):
        super().__init__(container)

        # this status is a boolean indicating whether the IP is currently reachable.
        self.status = False

        label = ttk.Label(self, text="Enter IP :")
        self.user_input = tk.StringVar()
        ip_entry = ttk.Entry(self, textvariable=self.user_input)
        self.status_label = tk.Label(self, text="Status")
        label.grid(row=0, column=0)
        ip_entry.grid(row=0, column=1)
        self.status_label.grid(row=0, column=2)

    def start_ping(self):
        z = subprocess.Popen(
            f"ping -n 1 {self.user_input.get()}", stdout=subprocess.PIPE
        )

        if b"Reply" in z.stdout.read():
            time.sleep(0.5)
            self.status = True
            self.status_label.config(bg="green")
        else:
            time.sleep(0.5)
            self.status = False
            self.status_label.config(bg="red")


class frameGeneratorFrame(ttk.Frame):
    def __init__(self, container):
        super().__init__(container)

        newFrame_button = ttk.Button(self, text="+ add IP", command=self.new_frame)
        newFrame_button.grid(row=100, column=0)

    def new_frame(self):
        x = ipFrame(root)
        x.grid()
        # Add this frame to the list of all generated frames
        root.list_of_generated_frames.append(x)


root = Root()
root.mainloop()
