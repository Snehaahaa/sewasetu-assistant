class FPDF:  # dev-only stub; production uses fpdf2
    def __getattr__(self, n): return lambda *a, **k: None
    def output(self): return b"%PDF-stub"
