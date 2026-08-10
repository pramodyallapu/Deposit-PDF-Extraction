from app.core.cpt_extraction import validate_date
from app.core.field_extraction import find_field_candidates

print(validate_date("01/15/2023"))  # Should print True
print(validate_date("15/01/2023"))  # Should print False 
print(validate_date("JULY 23, 2026"))
print(validate_date("15152025"))


print(find_field_candidates("Dateofremittance: JULY23,2026", "check_date"))  # Should find the date
