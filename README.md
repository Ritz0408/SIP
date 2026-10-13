# Best SIP Finder

Large Cap, Mid Cap aur Small Cap ke Direct Growth mutual funds ko rank karta hai.

**Live:** https://ritz0408.github.io/SIP/

## Ranking rules

1. Category choose karo: Large Cap / Mid Cap / Small Cap
2. Sirf Direct Plan + Growth funds (AMFI official category list)
3. Har fund ke liye 3 rankings:
   - **CAGR**: zyada = acha. 5Y CAGR; 5 saal se naye funds ka 3Y, woh bhi na ho to 1Y
   - **Exit Load**: kam = acha (pehle %, phir period, phir free units)
   - **Expense Ratio**: kam = acha
4. Tie ho to same rank (1, 1, 3)
5. **Total = teeno ranks ka sum**, sabse kam total = best fund
6. Min SIP sirf info ke liye

## Data sources

- [AMFI NAVAll.txt](https://portal.amfiindia.com/spages/NAVAll.txt): fund list + category
- [mfapi.in](https://www.mfapi.in/): NAV history, isse CAGR calculate hota hai
- Groww (unofficial): exit load, expense ratio, min SIP
- Kuvera mirror: fallback

GitHub Actions roz raat 9:30 baje (IST) data update karke page deploy karta hai.

## Local chalana

```
python app.py
```

Phir http://localhost:8000 kholo. Sirf Python standard library use hoti hai.

> Ye investment advice nahi hai. Invest karne se pehle fund ki site par details confirm karo.
