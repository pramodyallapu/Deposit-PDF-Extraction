# from app.core.cpt_extraction import validate_date
from app.core.field_extraction import find_field_candidates,extract_field

# print(validate_date("01/15/2023"))  # Should print True
# print(validate_date("15/01/2023"))  # Should print False 
# print(validate_date("JULY 23, 2026"))
# print(validate_date("15152025"))


# print(find_field_candidates("Dateofremittance: JULY23,2026", "check_date"))  # Should find the date

text = """
     BlueShieldofCalifornia                                                       
       POBOX241012                                                                  
       LODI, CA 95241-9512                                                          
                                                                                    
                                                          www.blueshieldca.com/provider
                                                                           Page: 1of 3
                                                                                    
                                                  ISSUEDATE:                 072826 
                                                  EOBNUMBER:        26209B10001839972548
                                                  PREFERREDPROVIDERYES              
       KEYCOMMUNICATIONSPEECHANDLANGUAGESERVICESINC PROVIDERNUMBER:      PG0119531001
       2910INLANDEMPIREBLVDSTE114                 PROVIDERNPI:             1225663958
       ONTARIO,CA91764-4896                       CHECKNUMBER:              31257085
                                                  CORRESPONDENCE:                   
                                                  POBOX1505,REDBLUFF,CA96080        
                                                  PHONE: (800)622-0632              
                                                                                1of 3
                                                                                    
                              EXPLANATION  OF BENEFITS                              
                THISISNOTABILL-RETAINFORPERSONALTAXANDMEDICALRECORDS                
                                                                                    
                                                                                    
  PATIENTNAME PATIENT DATES  PROCEDURE UNITS BILLED ALLOWED CONTRACTUAL NOTES DEDUCTIBLE CO-PAY AMOUNT
  I.D.NUMBER ACCOUNTNUMBER OF NUMBER OF     AMOUNT AMOUNT ADJUSTMENT     AMOUNT PAID
  GROUPNUMBER CLAIMNUMBER SERVICE    SERVICE            AMOUNT                      
  RECEIPTDATE: 07/17/26                                                             
  LIAMDEHORTA SP288380198FBFB7 06/30/26 92507GN 1 200.00 79.81 1     0.00 15.96 63.85
  PRXY00063 264913216300 07/07/26 92507GN 1  200.00 79.81     1      0.00 15.96 63.85
  ITSHOST1           07/14/26 92507GN 1      200.00 79.81     1      0.00 15.96 63.85
  TOTALS:                                    600.00      360.57      0.00 47.88 191.55
  NOTES:                                                                            
  1     CONTRACTINGPHYSICIANSANDHEALTHCAREPROVIDERSAGREETOACCEPTTHE ALLOWEDAMOUNTASPAYMENTINFULL. THESUBSCRIBERIS
        RESPONSIBLEONLYFORDEDUCTIBLESCOPAYMENTAMOUNTSANDNONCOVERED ITEMS.           
        YOURCONTRACTUALADJUSTMENTIS$360.57.                                         
        NOWVIEWORDOWNLOADYOUREOBSONLINE!SEARCHFORELIGIBILITYBENEFITSCLAIMSORAUTHORIZATIONSONLINEFORBLUESHIELDOTHER
        BLUEPLANANDFEDERALEMPLOYEEPROGRAMMEMBERS.USEOURBLUECARDCLAIMSROUTINGTOOLTOQUICKLYFINDOUTWHERETOSEND
        BLUECARDCLAIMS. FINDALLTHISANDMOREATBLUESHIELDCA.COM/PROVIDER.              
  RECEIPTDATE: 07/17/26                                                             
  LIAMDEHORTA SP288380301C1235 05/05/26 92507GN 1 200.00 79.81 1     79.81 0.00 0.00
  PRXY00215 264913218300 05/12/26 92507GN 1  200.00 79.81     1      79.81 0.00 0.00
  ITSHOST1           05/19/26 92507GN 1      200.00 79.81     1      79.81 0.00 0.00
                     06/09/26 92507GN 1      200.00 79.81     1      25.31 10.90 43.60
                     06/16/26 92507GN 1      200.00 79.81     1      0.00 15.96 63.85
                     06/23/26 92507GN 1      200.00 79.81     1      0.00 15.96 63.85
  CONTINUED...                                                                      
               THECHECKBELOWREPRESENTSPAYMENTFORCLAIMSITEMIZEDONTHISSTATEMENT       
                                                             BANKOFAMERICA  70-2328 
                                                          CommercialDisbursementAccount 719
                                                             NORTHBROOK,IL          
                                                          STANDARDCLAIMS-FACETS     
                                                        VOID12MONTHSFROMISSUEDATE   
     POBOX241012LODI,,CA95241-9512                                                  
                                                      PROVIDERNO.    CHECKNO.       
                                                     PG0119531001   31257085        
                                                      MO DAY YEAR  PAYDOLLARSCENTS  
                                                     07  28  26      $********362.85
       PAYTOTHEORDEROF                                                              
       KEYCOMMUNICATIONSPEECHANDLANGUAGESERVICESINC     ********362*DOLLARS*85*CTS* 
       2910INLANDEMPIREBLVDSTE114                                                   
       ONTARIO,CA91764-4896                                                         
                                                          VOID                      
              THIS IS NOT A CHECK,  NON-NEGOTIABLE                                  
             ⑈31257085⑈    ⑆071923284⑆     8765217671⑈                              
                                                                                   .
                                                                                  2/1
                                                                                  657520
                                                                                  AGGMOCOOIOOGGCOKACCMMOKCAMAMIAOK ALGPBPHMHLGPFJBKAPFIEIDNGLEPAPGK
       ------ manifest line ---------                                               
       DTTAFADDTTFTDTFTFDTDDADADAFADFATDDFTAAAFDTTADFAAATDFDTDFADDDTDFFT            
                                                                                  ---
                                                                                  stresni
                                                                                  on
                                                                                  ---     
   """

result = extract_field(text, "check_amount")
print(result)