# from app.core.cpt_extraction import validate_date
from app.core.field_extraction import extract_field

data ="""
     &&$$2 $$EPPEPP000000768951006                                                    
                                                                                    
      Luminare Health                                                               
      PO Box 2920                                                                   
      Clinton, IA 52733-2920                                                        
                                                                                    
                                                                                    
                                                                                    
                                                                                    
                                                                                    
                                                                                    
      Theralympic Speech                                                            
      622 HAWKINS AVE                                                               
      RONKONKOMA NY 11779-2374                                                      
                                                                                    
                                                                                    
                                                           Payment Questions? Please refer to the
     Your name, Theralympic Speech, and Tax ID have been verified by the IRS. customer service numbers below
                                                                                    
   Tax ID: 823478182 EPC Draft #: 408350636 Payment Week: 27 Payment Date: 07/07/2026 Page 1 of 2
    Service Date Code or Description Explanation Codes Total Charge Provider Other Plan Other Patient Obligation Net Payment
                                       Discount Payment Adjustment Ineligible Co-Pay Deductible Co-Ins Amount
   Provider: EMILY QUIGLEY, OT    Patient Acct #: THERA101638 Group/Check Number: ECM0757/0000151473
   Network: AETNA                 Member/Patient ID: 71300883C Customer Service:866-893-4472
   Patient Name: GREYSON HEGARTY  Claim #: 061126-183-29 Administered By: Luminare Health
   05/13/26 97530(GO) AET          200.00 164.22 0.00 0.00 0.00 0.00 0.00 0.00 35.78
   05/13/26 97110(GO) AET          200.00 170.32 0.00 0.00 0.00 0.00 0.00 0.00 29.68
   05/15/26 97530(GO) AET          200.00 164.22 0.00 0.00 0.00 0.00 0.00 0.00 35.78
   05/15/26 97110(GO) AET          200.00 170.32 0.00 0.00 0.00 0.00 0.00 0.00 29.68
   05/20/26 97530(GO) AET          200.00 164.22 0.00 0.00 0.00 0.00 0.00 0.00 35.78
   05/20/26 97110(GO) AET          200.00 170.32 0.00 0.00 0.00 0.00 0.00 0.00 29.68
                           Claim Total: 1,200.00 1,003.62 0.00 0.00 0.00 0.00 0.00 0.00 196.38
   Provider: EMILY QUIGLEY, OT    Patient Acct #: THERA101639 Group/Check Number: ECM0757/0000151473
   Network: AETNA                 Member/Patient ID: 71300883C Customer Service:866-893-4472
   Patient Name: GREYSON HEGARTY  Claim #: 061126-183-30 Administered By: Luminare Health
   05/22/26 97530(GO) AET          200.00 164.22 0.00 0.00 0.00 0.00 0.00 0.00 35.78
   05/22/26 97110(GO) AET          200.00 170.32 0.00 0.00 0.00 0.00 0.00 0.00 29.68
                           Claim Total: 400.00 334.54 0.00 0.00 0.00 0.00 0.00 0.00 65.46
              Statement Summary Total Charge Provider Other Plan Other Patient Obligation Net Payment
   Administered By                     Discount Payment Adjustment Ineligible Co -Pay Deductible Co-Ins Amount
   Luminare Health                 1,600.00 1,338.16 0.00 0.00 0.00 0.00 0.00 0.00 261.84
                                                                                    
   Explanations                                                                     
   Administered By Codes Description                                                
   Luminare Health AET PATIENT IS NOT RESPONSIBLE FOR AETNA PPO DISCOUNT.              
                                                                              
   &&$$1                                                                                
                                                                                    
                                         HUNTINGTON NATIONAL BANK 56-1512 DRAFT NO. 408350636
     Luminare Health                                      441                       
                                            Westerville OH 43081 DRAFT DATE 07/07/2026
     PO Box 2920                                                                    
                                            Electronic Payment Clearinghouse        
     Clinton, IA 52733-2920                   Echo Health, Inc.                     
     PAYABLE Two Hundred Sixty-One & 84 / 100 DOLLARS                               
                                                                  AMOUNT            
     THROUGH                                                                        
                                                                  **********$261.84 
     DRAFT                                                                          
     TO THE  Theralympic Speech                              VOID AFTER 180 DAYS    
     ORDER OF 622 HAWKINS AVE                                                       
             RONKONKOMA NY 11779-2374                                               
               C408350636C        A044115126A  01669508612C 
"""

result = extract_field(data, "check_amount")
print(result)