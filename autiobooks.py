import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import sys
import threading
import io
import ebooklib
from PIL import Image, ImageTk
from engine import main


class TextRedirector:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, str):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, str)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state='disabled')

    def flush(self):
        pass


LANGUAGE_TO_FLAG = {
    "en-us": "🇺🇸",
    "en-gb": "🇬🇧",
    "fr-fr": "🇫🇷",
    "ja": "🇯🇵",
    "ko": "🇰🇷",
    "cmn": "🇨🇳"
}


def get_language_from_voice(voice):
    if voice.startswith("a"):
        return "en-us"
    elif voice.startswith("b"):
        return "en-gb"
    elif voice.startswith("e"):
        return "es"
    elif voice.startswith("f"):
        return "fr-fr"
    elif voice.startswith("h"):
        return "hi"
    elif voice.startswith("i"):
        return "it"
    elif voice.startswith("j"):
        return "ja"
    elif voice.startswith("p"):
        return "pt-br"
    elif voice.startswith("z"):
        return "cmn"
    else:
        print("Voice not recognized.")
        exit(1)


def emojify_voice(voice):
    language = get_language_from_voice(voice)
    if language in LANGUAGE_TO_FLAG:
        return LANGUAGE_TO_FLAG[language] + " " + voice
    return voice


def deemojify_voice(voice):
    if voice[:2] in LANGUAGE_TO_FLAG.values():
        return voice[3:]
    return voice


def start_gui():
    root = tk.Tk()
    root.title('Autiobooks')
    root.geometry('1200x900')
    root.resizable(False, False)

    voice_frame = tk.Frame(root)
    voice_frame.pack(pady=5, padx=5)

    # add a scale to set speed
    speed_label = tk.Label(voice_frame, text="Set speed:", font=('Arial', 12))
    speed_label.pack(side=tk.LEFT, pady=5, padx=5)

    def check_speed_range(event=None):
        try:
            value = float(speed_entry.get())
            if 0.5 <= value <= 2.0:
                speed_entry.configure(fg='white')
                return True
            else:
                speed_entry.configure(fg='red')
        except ValueError:
            speed_entry.configure(fg='red')
        return False

    speed_entry = tk.Entry(
        voice_frame,
        width=5,
        font=('Arial', 12)
    )
    speed_entry.insert(0, "1.0")
    speed_entry.pack(side=tk.LEFT, pady=5, padx=5)
    speed_entry.bind('<KeyRelease>', check_speed_range)

    # add a tickbox to enable/disable GPU acceleration
    gpu_acceleration = tk.BooleanVar()
    gpu_acceleration.set(False)
    gpu_acceleration_checkbox = tk.Checkbutton(
        voice_frame,
        text="Enable GPU acceleration",
        variable=gpu_acceleration,
        font=('Arial', 12)
    )
    mac_os = sys.platform == 'darwin'
    if not mac_os:
        gpu_acceleration_checkbox.pack(side=tk.LEFT, pady=5, padx=5)
    
    # add a combo box with voice options
    voice_label = tk.Label(voice_frame, text="Select Voice:", font=('Arial', 12))
    voice_label.pack(side=tk.LEFT, pady=5, padx=5)

    # add a combo box with voice options
    from voices import voices
    voices = [emojify_voice(x) for x in voices]
    voice_combo = ttk.Combobox(
        voice_frame,
        values=voices,
        state="readonly",
        font=('Arial', 12)
    )
    voice_combo.set(voices[0])  # Set default selection
    voice_combo.pack(side=tk.LEFT, pady=10, padx=5)

    # ui element variables
    pil_image = Image.new('RGB', (200, 300), 'gray')
    cover_image = ImageTk.PhotoImage(pil_image)  # or use a default image
    cover_label = tk.Label(root, image=cover_image)
    chapters = []

    def resized_image(item):
        image_data = item.get_content()
        image = Image.open(io.BytesIO(image_data))
        image.thumbnail((200, 300))
        ratio = min(200/image.width, 300/image.height)
        new_size = (int(image.width * ratio), int(image.height * ratio))
        # Resize with high-quality resampling
        resized = image.resize(new_size, Image.Resampling.LANCZOS)
        # Create new image with gray background
        background = Image.new('RGB', (200, 300), 'gray')
        # Paste resized image centered
        offset = ((200 - new_size[0])//2, (300 - new_size[1])//2)
        background.paste(resized, offset)
        return ImageTk.PhotoImage(background)

    def get_cover_image(book):
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_COVER:
                return resized_image(item)
            if item.get_type() == ebooklib.ITEM_IMAGE:
                if 'cover' in item.get_name().lower():
                    return resized_image(item)
        return None
    
    def add_chapters_to_checkbox_frame():
        for chapter in chapters:
            var = tk.BooleanVar()
            checkbox = tk.Checkbutton(
                checkbox_frame,
                text=chapter,
                variable=var,
                font=('Arial', 12)
            )
            checkbox.pack(anchor="w")
            checkbox_vars[chapter] = var
    
    def remove_chapters_from_checkbox_frame():
        for widget in checkbox_frame.winfo_children():
            widget.destroy()
        checkbox_vars.clear()

    def select_file():
        file_path = filedialog.askopenfilename(
            title='Select an epub file',
            filetypes=[('epub files', '*.epub')]
        )
        if file_path:
            file_label.config(text=file_path)
            book = ebooklib.epub.read_epub(file_path)
            cover_image_from_book = get_cover_image(book)
            if cover_image_from_book:
                cover_label.image = cover_image_from_book
                cover_label.configure(image=cover_image_from_book)
            else:
                cover_label.image = cover_image
                cover_label.configure(image=cover_image)
            
            # set chapters
            chapters.clear()
            remove_chapters_from_checkbox_frame()
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    chapters.append(item.get_name())
            chapters.sort()
            add_chapters_to_checkbox_frame()
            
    
    def convert():
        def enable_controls():
            speed_entry.configure(state='normal')
            voice_combo.configure(state='normal')
        
        def run_conversion():
            try:
                chapters_selected = [chapter for chapter, var in checkbox_vars.items() if var.get()]
                enable_gpu = gpu_acceleration.get()
                main(file_path, voice, float(speed), chapters_selected, enable_gpu)
            finally:
                # Ensure controls are re-enabled even if an error occurs
                root.after(0, enable_controls)
        
        if not check_speed_range():
            warning = "Please enter a speed value between 0.5 and 2.0."
            print(warning)
            # create a warning message box to say this
            messagebox.showwarning("Warning", warning)

        if file_label.cget("text"):
            output_text.configure(state='normal')
            output_text.delete(1.0, tk.END)
            output_text.configure(state='disabled')
            # Redirect stdout to Text widget
            sys.stdout = TextRedirector(output_text)
            file_path = file_label.cget("text")
            voice = deemojify_voice(voice_combo.get())
            speed = speed_entry.get()
            speed_entry.configure(state='disabled')
            voice_combo.configure(state='disabled')
            threading.Thread(target=run_conversion).start()
            # when this thread finishes, re-enable the buttons

        else:
            warning = "Please select an epub file first."
            print(warning)
            # create a warning message box to say this
            messagebox.showwarning("Warning", warning)

    file_frame = tk.Frame(root)
    file_frame.pack(pady=5)

    file_button = tk.Button(
        file_frame,
        text='Select epub file',
        command=select_file,
        bg='white',
        fg='black',
        font=('Arial', 12)
    )
    file_button.pack(side=tk.LEFT, pady=5, padx=5)

    file_label = tk.Label(file_frame, text="")
    file_label.pack(side=tk.LEFT, pady=5)

    cover_label.image = cover_image  # Keep a reference to prevent garbage collection
    cover_label.pack(pady=10)

    # Create main container frame
    container = tk.Frame(root)
    container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    # Create canvas and scrollbar
    canvas = tk.Canvas(container)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    
    # Create frame for checkboxes inside canvas
    checkbox_frame = tk.Frame(canvas)
    checkbox_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    
    # Add frame to canvas
    canvas.create_window((0, 0), window=checkbox_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    
    # Pack scrollbar and canvas
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    
    # Dictionary to store checkbox variables
    checkbox_vars = {}

    start_convert_button = tk.Button(
        root,
        text='Convert epub',
        command=convert,
        bg='white',
        fg='black',
        font=('Arial', 12)
    )
    start_convert_button.pack(pady=20)

    output_text = tk.Text(root, height=10, width=50, bg="black", fg="white", font=('Arial', 12))
    output_text.pack(pady=20, padx=20, fill=tk.BOTH, expand=True)
    output_text.tag_configure("red", foreground="white")
    output_text.insert(tk.END, "Output here....", "red")
    output_text.configure(state='disabled')

    # start main loop
    root.mainloop()


if __name__ == "__main__":
    start_gui()
