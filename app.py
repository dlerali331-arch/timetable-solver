import os
from flask import Flask, request, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from ortools.sat.python import cp_model

app = Flask(__name__)

# IDی گووگڵ شیتەکە
SPREADSHEET_ID = "1Tt4E6nmGvvxJgHJYzRsenAR6Pda3Ke4Oobr9gJonoSM"

def run_timetable_solver():
    # بەستنەوە بە Google Sheets API
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # بەکارهێنانی فایلی credentials لە ڕێگەی Environment Variables یان فایلی لۆکاڵ
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        import json
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    master_sheet = spreadsheet.worksheet("Master_Timetable")
    class_sheet = spreadsheet.worksheet("Class_Mapping")
    days_sheet = spreadsheet.worksheet("School_Days")
    teacher_sheet = spreadsheet.worksheet("Teacher_Constraints")

    # 1. خوێندنەوەی ڕۆژە چالاکەکان
    days_data = days_sheet.get_all_values()[1:]
    active_days = []
    for row in days_data:
        if len(row) > 1:
            day_name = row[1].strip()
            is_active = str(row[2]).upper() if len(row) > 2 else 'TRUE'
            if is_active in ['TRUE', '1', '']:
                active_days.append(day_name)

    num_days = len(active_days)
    periods_per_day = 6 

    # 2. خوێندنەوەی پۆلەکان
    classes_data = class_sheet.get_all_values()[1:]
    classes_list = []
    for row in classes_data:
        pole = row[0].strip()
        section = row[1].strip() if len(row) > 1 else ""
        if pole:
            full_c = f"{pole} {section}".strip()
            if full_c not in classes_list:
                classes_list.append(full_c)

    # 3. خوێندنەوەی مەرجی مامۆستایان
    teacher_constraints = {}
    teacher_data = teacher_sheet.get_all_values()[1:]

    for row in teacher_data:
        if not row or not row[0].strip():
            continue
        t_name = row[0].strip()
        max_days = int(row[1]) if len(row) > 1 and str(row[1]).isdigit() else num_days
        max_per_day = int(row[2]) if len(row) > 2 and str(row[2]).isdigit() else periods_per_day
        
        teacher_constraints[t_name] = {
            "max_days": max_days,
            "max_per_day": max_per_day,
            "assignments": []
        }

    # 4. پشکنینی ڕاستەوخۆ بۆ نەگونجان (Infeasibility Pre-check)
    infeasibility_issues = []
    for t_name, data in teacher_constraints.items():
        total_required_lessons = sum([a[2] for a in data["assignments"]])
        max_possible_lessons = data["max_days"] * data["max_per_day"]
        
        if total_required_lessons > max_possible_lessons:
            issue_msg = (f"کێشەی نەگونجان: مامۆستا ({t_name}) {total_required_lessons} بەشەوانەی هەیە، "
                         f"بەڵام بەپێی ڕۆژەکانی دەوام ({data['max_days']} ڕۆژ) تەنها توانای گرتنی {max_possible_lessons} وانەی هەیە!")
            infeasibility_issues.append(issue_msg)

    if infeasibility_issues:
        return {"status": "error", "message": "نەگونجانی داتا دۆزرایەوە", "details": infeasibility_issues}

    # 5. شیکارکردن بە Google OR-Tools (CP-SAT Solver)
    model = cp_model.CpModel()
    grid = {}

    for day_idx, day in enumerate(active_days):
        for p in range(1, periods_per_day + 1):
            for c_code in classes_list:
                for t_name in teacher_constraints.keys():
                    grid[(day_idx, p, c_code, t_name)] = model.NewBoolVar(f"x_{day_idx}_{p}_{c_code}_{t_name}")

    # مەرجی ۱: تەنها ۱ وانە بۆ یەک پۆل لە یەک کاتدا
    for day_idx in range(num_days):
        for p in range(1, periods_per_day + 1):
            for c_code in classes_list:
                model.AddAtMostOne(grid[(day_idx, p, c_code, t_name)] for t_name in teacher_constraints.keys())

    # مەرجی ۲: مامۆستایەک نەچێتە دوو پۆل لە یەک کاتدا
    for day_idx in range(num_days):
        for p in range(1, periods_per_day + 1):
            for t_name in teacher_constraints.keys():
                model.AddAtMostOne(grid[(day_idx, p, c_code, t_name)] for c_code in classes_list)

    # مەرجی ۳: وانەی شاغر (بەتاڵ) نەکەوێتە وانەی ١ و ۲
    for day_idx in range(num_days):
        for c_code in classes_list:
            for p in [1, 2]:
                model.Add(sum(grid[(day_idx, p, c_code, t_name)] for t_name in teacher_constraints.keys()) == 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        new_rows = [["ID", "Day", "Period", "Class", "Subject", "Teacher"]]
        for day_idx, day in enumerate(active_days):
            for p in range(1, periods_per_day + 1):
                for c_code in classes_list:
                    for t_name, data in teacher_constraints.items():
                        if solver.Value(grid[(day_idx, p, c_code, t_name)]) == 1:
                            subj_name = "وانە"
                            for c, subj, _ in data["assignments"]:
                                if c == c_code:
                                    subj_name = subj
                                    break
                            new_rows.append(["", day, f"وانەی {p}", c_code, subj_name, t_name])

        master_sheet.clear()
        master_sheet.update('A1', new_rows)
        return {"status": "success", "message": "خشتەکە بە سەرکەوتوویی لە Google Sheets نوێکرایەوە!"}
    else:
        return {"status": "failed", "message": "هیچ خشتەیەکی گونجاو بەپێی ئەم مەرجانە نەدۆزرایەوە."}

@app.route('/', methods=['GET', 'POST'])
def generate_timetable():
    result = run_timetable_solver()
    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
