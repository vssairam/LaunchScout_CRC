### **LaunchScout: Competitor Research Crew**



A team of four AI agents that researches the market for a new business idea and delivers a competitor report with prices, gaps and a launch plan



**The real problem**



A founder wants to launch a new business, for example a healthy millet meal box service in Coimbatore. Before

spending money, they need to know: Who are the competitors? What do they charge? What do their customers

complain about? Where is the gap? Today this takes a marketing intern 2 to 3 days of searching and copying into

Excel.



**Who uses it**: founders, marketing teams, business consultants, students preparing pitch decks.



What you will build



A Crew of four agents, each with its own role, goal, backstory and tools:



**Agent** 				**Role and goal** 										**Tools**	

Market Researcher 		Find 3 to 5 real competitors with prices, offers and sales channels			Web search (Serper), website scraper

Review Analyst 			Find what customers like and complain about for each competitor				Web search, scraper

Strategy Analyst 		Build a comparison matrix, find the market gap, suggest price and positioning		None (reasons on the work of the first two agents)

Report Writer 			Write a clean, founder-friendly report with sources 					File writer (saves report.md)



• **Input form**: business idea, city, target customer, optional known competitors.

• **Live progress**: show which agent is working and what it found.

• **Final report**: displayed in the app and downloadable as Markdown or PDF.

• **Sources**: every price or claim links back to the page it came from.



**How it works**



**01** **Idea input** Founder fills the form	›	**02** **Research** Researcher finds competitors and prices	›	**03** **Reviews** Analyst finds customer pain points

› 	**04** **Strategy** Strategist builds matrix and gaps	›	**05** **Write** Writer creates the final report	›	**06** **Deliver** View in app and download



Tasks run in sequential order. Each task receives the previous task's output as context, just like a real team passing work along





**Tech stack and key concepts to use**

**Part** 				**Use** 							**What you learn**

CrewAI Agent 			role, goal, backstory, tools, llm 			Designing specialised agents

CrewAI Task 			description, expected\_output, context, agent 		Writing tasks with a clear, checkable output

Crew + Process 			Process.sequential (stretch: hierarchical) 		Controlling how agents collaborate

Tools 				SerperDevTool, ScrapeWebsiteTool, FileWriterTool 	Giving agents real access to the web

Output 				output\_file, markdown to PDF 				Producing a deliverable, not just chat text

UI 				Streamlit with live step log 				Making agent work visible to the use





**Sample input**

**Form input**

Business idea   		: Healthy millet meal boxes delivered for lunch

City            		: Coimbatore

Target customer 		: Office workers, age 25 to 40

Known competitors (optional)	: leave blank





**Sample output**

Competitor names and numbers below are an illustrative sample of the format. Your crew must use real, sourced data.

report.md (excerpt)



\# Competitor Report: Healthy Millet Meal Boxes, Coimbatore

&#x20;

**## 1. Key Competitors**

| Competitor        | Price / meal | Channel        | Strength          | Weakness         |

|-------------------|--------------|----------------|-------------------|------------------|

| GreenBowl Kitchen | Rs 180       | Swiggy, Zomato | Strong branding   | Same menu daily  |

| MilletMan Meals   | Rs 150       | WhatsApp order | Low price         | Late delivery    |

| FitPlate Express  | Rs 240       | Own app        | Calorie tracking  | Expensive        |

&#x20;

**## 2. What Customers Complain About**

Same menu every day, gets boring in a week (GreenBowl, MilletMan)- No monthly subscription option (all three)

&#x20;

**## 3. Market Gap**

No one offers a rotating weekly menu with a monthly office subscription

under Rs 200 per meal.

&#x20;

**## 4. Recommended Positioning**

"Fresh millet lunch, new menu every day, Rs 3,999 per month"

&#x20;

**## 5. 30-Day Launch Plan**

Week 1: Tie up with 3 IT parks ... Week 4: Collect 50 review



**# Sources**

\[1] swiggy.com/... \[2] google.com/maps/... \[3] instagram.com/...







**Output explained**

**Report section** 				**Produced by** 				**What it tells the founder**

Key Competitors 			Market Researcher 			Who is already selling, at what price, on which platform

Customer complaints 			Review Analyst 				The pain points you can solve better than them

Market Gap 				Strategy Analyst 			The one opportunity nobody is serving well today

Recommended Positioning 		Strategy Analyst 			A clear one-line offer with a price

30-Day Launch Plan 			Report Writer 				Practical first steps, week by week

Sources 				All agents 				Proof: the founder can verify every number





**Done checklist**

* Four agents with clearly different roles, goals and backstories.
* Every task has an expected\_output that describes the exact format required.
* At least 3 competitors found, each with a price and at least one source link.
* Report contains all 6 sections and is saved to a file.
* The app shows which agent is working while the crew runs.
* Works for a different idea and city without changing any code.



**Stretch goals**

Switch to a hierarchical process with a Manager agent | Add a Pitch Deck agent that turns the report into 6 slides | Re-run the

crew weekly and highlight price changes.





