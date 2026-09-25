from fpdf import FPDF


def create_pdf(period, reuma_input, nero_input, episkeves_input=0.0, repair_name="Repairs"):
    # Μαθηματικοί υπολογισμοί
    r_a1 = reuma_input * 0.2901
    r_b1 = reuma_input * 0.2472
    r_b2 = reuma_input * 0.4627

    n_share = nero_input / 3
    e_share = episkeves_input / 3  # Επιμερισμός επισκευών στα 3 διαμερίσματα

    total_a1 = r_a1 + 6.70 + 10.33 + n_share + e_share
    total_b1 = r_b1 + 6.70 + 11.50 + n_share + e_share
    total_b2 = r_b2 + 6.70 + 11.50 + n_share + e_share
    grand_total = reuma_input + 20.00 + 33.33 + nero_input + episkeves_input

    pdf = FPDF()
    pdf.add_page()

    # Τίτλος
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(190, 10, txt="KOINOXRISTA FILIKON", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(190, 10, txt=f"PERIOD: {period}", ln=True, align="C")
    pdf.ln(5)

    col_widths = [45, 30, 38, 38, 38]
    h = 10

    # Κεφαλίδα
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(0, 102, 204)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(col_widths[0], h, "Description", 1, 0, "C", True)
    pdf.cell(col_widths[1], h, "Total (Euro)", 1, 0, "C", True)
    pdf.cell(col_widths[2], h, "Apartment A1", 1, 0, "C", True)
    pdf.cell(col_widths[3], h, "Apartment B1", 1, 0, "C", True)
    pdf.cell(col_widths[4], h, "Apartment B2", 1, 1, "C", True)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)

    # Electricity
    pdf.cell(col_widths[0], h, "Electricity", 1)
    pdf.cell(col_widths[1], h, f"{reuma_input:.2f}", 1, 0, "R")
    pdf.cell(col_widths[2], h, f"{r_a1:.2f}", 1, 0, "R")
    pdf.cell(col_widths[3], h, f"{r_b1:.2f}", 1, 0, "R")
    pdf.cell(col_widths[4], h, f"{r_b2:.2f}", 1, 1, "R")

    # Cleaning
    pdf.cell(col_widths[0], h, "Cleaning", 1)
    pdf.cell(col_widths[1], h, "20.00", 1, 0, "R")
    pdf.cell(col_widths[2], h, "6.70", 1, 0, "R")
    pdf.cell(col_widths[3], h, "6.70", 1, 0, "R")
    pdf.cell(col_widths[4], h, "6.70", 1, 1, "R")

    # Elevator
    pdf.cell(col_widths[0], h, "Elevator Maint.", 1)
    pdf.cell(col_widths[1], h, "33.33", 1, 0, "R")
    pdf.cell(col_widths[2], h, "10.33", 1, 0, "R")
    pdf.cell(col_widths[3], h, "11.50", 1, 0, "R")
    pdf.cell(col_widths[4], h, "11.50", 1, 1, "R")

    # Water
    pdf.cell(col_widths[0], h, "Water", 1)
    pdf.cell(col_widths[1], h, f"{nero_input:.2f}", 1, 0, "R")
    pdf.cell(col_widths[2], h, f"{n_share:.2f}", 1, 0, "R")
    pdf.cell(col_widths[3], h, f"{n_share:.2f}", 1, 0, "R")
    pdf.cell(col_widths[4], h, f"{n_share:.2f}", 1, 1, "R")

    # Dynamic Repairs Name
    repair_label = f"Repairs ({repair_name})" if repair_name and repair_name != "Repairs" else "Repairs"
    pdf.cell(col_widths[0], h, repair_label[:22], 1)
    pdf.cell(col_widths[1], h, f"{episkeves_input:.2f}", 1, 0, "R")
    pdf.cell(col_widths[2], h, f"{e_share:.2f}", 1, 0, "R")
    pdf.cell(col_widths[3], h, f"{e_share:.2f}", 1, 0, "R")
    pdf.cell(col_widths[4], h, f"{e_share:.2f}", 1, 1, "R")

    # Grand Total
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(204, 229, 255)
    pdf.cell(col_widths[0], h, "GRAND TOTAL", 1, 0, "L", True)
    pdf.cell(col_widths[1], h, f"{grand_total:.2f}", 1, 0, "R", True)
    pdf.cell(col_widths[2], h, f"{total_a1:.2f}", 1, 0, "R", True)
    pdf.cell(col_widths[3], h, f"{total_b1:.2f}", 1, 0, "R", True)
    pdf.cell(col_widths[4], h, f"{total_b2:.2f}", 1, 1, "R", True)

    # Διορθωμένο return για fpdf2
    return bytes(pdf.output())