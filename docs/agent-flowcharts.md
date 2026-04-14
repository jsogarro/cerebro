# Agent Decision Process Flowcharts

This document contains detailed flowcharts for each agent's decision-making process in the Multi-Agent Research Platform.

## Table of Contents
- [Literature Review Agent Flow](#literature-review-agent-flow)
- [Comparative Analysis Agent Flow](#comparative-analysis-agent-flow)
- [Methodology Agent Flow](#methodology-agent-flow)
- [Synthesis Agent Flow](#synthesis-agent-flow)
- [Citation & Verification Agent Flow](#citation--verification-agent-flow)
- [Agent Coordination Flow](#agent-coordination-flow)
- [Conflict Resolution Flow](#conflict-resolution-flow)
- [Quality Assurance Flow](#quality-assurance-flow)

## Literature Review Agent Flow

```mermaid
flowchart TD
    START([Start Literature Review]) --> INPUT[Receive Research Query]
    INPUT --> PARSE[Parse Query Components]
    
    PARSE --> KEYWORDS[Extract Keywords]
    KEYWORDS --> EXPAND[Expand with Synonyms]
    EXPAND --> QUERIES[Generate Search Queries]
    
    QUERIES --> SEARCH{Select Search Strategy}
    
    SEARCH -->|Academic| ACADEMIC[Search Academic DBs]
    SEARCH -->|General| GENERAL[Search General Sources]
    SEARCH -->|Specialized| SPECIAL[Search Domain DBs]
    
    ACADEMIC --> SCHOLAR[Google Scholar]
    ACADEMIC --> PUBMED[PubMed]
    ACADEMIC --> ARXIV[arXiv]
    
    GENERAL --> WEB[Web Search]
    GENERAL --> BOOKS[Google Books]
    
    SPECIAL --> IEEE[IEEE Xplore]
    SPECIAL --> ACM[ACM Digital]
    
    SCHOLAR --> COLLECT[Collect Results]
    PUBMED --> COLLECT
    ARXIV --> COLLECT
    WEB --> COLLECT
    BOOKS --> COLLECT
    IEEE --> COLLECT
    ACM --> COLLECT
    
    COLLECT --> DEDUPE[Remove Duplicates]
    DEDUPE --> RELEVANCE{Check Relevance}
    
    RELEVANCE -->|Low| FILTER[Filter Out]
    RELEVANCE -->|High| RANK[Rank by Quality]
    
    FILTER --> MORE{Need More?}
    MORE -->|Yes| SEARCH
    MORE -->|No| END_SEARCH[End Search]
    
    RANK --> EXTRACT[Extract Key Info]
    EXTRACT --> TITLE[Title & Authors]
    EXTRACT --> ABSTRACT[Abstract]
    EXTRACT --> FINDINGS[Key Findings]
    EXTRACT --> METHODS[Methodology]
    
    TITLE --> COMPILE[Compile Review]
    ABSTRACT --> COMPILE
    FINDINGS --> COMPILE
    METHODS --> COMPILE
    
    COMPILE --> VALIDATE{Validate Quality}
    VALIDATE -->|Pass| EMBED[Generate Embeddings]
    VALIDATE -->|Fail| RETRY{Retry Count}
    
    RETRY -->|< 3| SEARCH
    RETRY -->|>= 3| ERROR[Report Error]
    
    EMBED --> STORE[Store in Vector DB]
    STORE --> FORMAT[Format Output]
    FORMAT --> COMPLETE([Complete])
    ERROR --> COMPLETE
```

## Comparative Analysis Agent Flow

```mermaid
flowchart TD
    START([Start Comparative Analysis]) --> RECEIVE[Receive Literature Data]
    RECEIVE --> CATEGORIZE[Categorize Papers]
    
    CATEGORIZE --> THEORIES[Group by Theory]
    CATEGORIZE --> METHODS[Group by Method]
    CATEGORIZE --> FINDINGS[Group by Findings]
    CATEGORIZE --> TIMELINE[Group by Timeline]
    
    THEORIES --> IDENTIFY[Identify Comparison Points]
    METHODS --> IDENTIFY
    FINDINGS --> IDENTIFY
    TIMELINE --> IDENTIFY
    
    IDENTIFY --> DIMENSIONS{Select Dimensions}
    
    DIMENSIONS -->|Theoretical| THEO_COMP[Compare Theories]
    DIMENSIONS -->|Empirical| EMP_COMP[Compare Evidence]
    DIMENSIONS -->|Methodological| METH_COMP[Compare Methods]
    
    THEO_COMP --> SIMILARITIES[Find Similarities]
    THEO_COMP --> DIFFERENCES[Find Differences]
    THEO_COMP --> GAPS[Identify Gaps]
    
    EMP_COMP --> CONSISTENCY[Check Consistency]
    EMP_COMP --> CONTRADICTIONS[Find Contradictions]
    EMP_COMP --> STRENGTH[Assess Strength]
    
    METH_COMP --> APPROACHES[Compare Approaches]
    METH_COMP --> VALIDITY[Assess Validity]
    METH_COMP --> LIMITATIONS[Note Limitations]
    
    SIMILARITIES --> MATRIX[Build Comparison Matrix]
    DIFFERENCES --> MATRIX
    GAPS --> MATRIX
    CONSISTENCY --> MATRIX
    CONTRADICTIONS --> MATRIX
    STRENGTH --> MATRIX
    APPROACHES --> MATRIX
    VALIDITY --> MATRIX
    LIMITATIONS --> MATRIX
    
    MATRIX --> PATTERNS{Identify Patterns}
    
    PATTERNS -->|Found| ANALYZE[Analyze Patterns]
    PATTERNS -->|None| REPORT_NONE[Report No Patterns]
    
    ANALYZE --> TRENDS[Extract Trends]
    ANALYZE --> CONSENSUS[Find Consensus]
    ANALYZE --> DEBATES[Identify Debates]
    
    TRENDS --> SYNTHESIZE[Synthesize Comparisons]
    CONSENSUS --> SYNTHESIZE
    DEBATES --> SYNTHESIZE
    REPORT_NONE --> SYNTHESIZE
    
    SYNTHESIZE --> VISUALIZE{Create Visualizations}
    
    VISUALIZE -->|Tables| TABLES[Comparison Tables]
    VISUALIZE -->|Charts| CHARTS[Charts & Graphs]
    VISUALIZE -->|Matrices| MATRICES[Decision Matrices]
    
    TABLES --> OUTPUT[Format Output]
    CHARTS --> OUTPUT
    MATRICES --> OUTPUT
    
    OUTPUT --> QUALITY{Quality Check}
    QUALITY -->|Pass| DELIVER[Deliver Results]
    QUALITY -->|Fail| REFINE[Refine Analysis]
    
    REFINE --> MATRIX
    DELIVER --> END([Complete])
```

## Methodology Agent Flow

```mermaid
flowchart TD
    START([Start Methodology Analysis]) --> CONTEXT[Receive Research Context]
    CONTEXT --> DOMAIN[Identify Domain]
    
    DOMAIN --> TYPE{Research Type?}
    
    TYPE -->|Quantitative| QUANT[Quantitative Methods]
    TYPE -->|Qualitative| QUAL[Qualitative Methods]
    TYPE -->|Mixed| MIXED[Mixed Methods]
    
    QUANT --> EXPERIMENTAL[Experimental Design]
    QUANT --> SURVEY[Survey Methods]
    QUANT --> STATISTICAL[Statistical Analysis]
    
    QUAL --> INTERVIEWS[Interview Protocols]
    QUAL --> OBSERVATIONS[Observation Methods]
    QUAL --> CONTENT[Content Analysis]
    
    MIXED --> SEQUENTIAL[Sequential Design]
    MIXED --> CONCURRENT[Concurrent Design]
    MIXED --> TRANSFORMATIVE[Transformative Framework]
    
    EXPERIMENTAL --> DESIGN[Select Design]
    SURVEY --> DESIGN
    STATISTICAL --> DESIGN
    INTERVIEWS --> DESIGN
    OBSERVATIONS --> DESIGN
    CONTENT --> DESIGN
    SEQUENTIAL --> DESIGN
    CONCURRENT --> DESIGN
    TRANSFORMATIVE --> DESIGN
    
    DESIGN --> SAMPLE{Sampling Strategy}
    
    SAMPLE -->|Random| RANDOM[Random Sampling]
    SAMPLE -->|Stratified| STRATIFIED[Stratified Sampling]
    SAMPLE -->|Purposive| PURPOSIVE[Purposive Sampling]
    SAMPLE -->|Convenience| CONVENIENCE[Convenience Sampling]
    
    RANDOM --> SIZE[Calculate Sample Size]
    STRATIFIED --> SIZE
    PURPOSIVE --> SIZE
    CONVENIENCE --> SIZE
    
    SIZE --> POWER{Power Analysis}
    POWER -->|Adequate| PROCEED[Proceed]
    POWER -->|Inadequate| ADJUST[Adjust Size]
    
    ADJUST --> SIZE
    PROCEED --> INSTRUMENTS[Select Instruments]
    
    INSTRUMENTS --> VALIDITY_CHECK{Check Validity}
    VALIDITY_CHECK -->|Valid| RELIABLE{Check Reliability}
    VALIDITY_CHECK -->|Invalid| ALTERNATIVE[Find Alternatives]
    
    ALTERNATIVE --> INSTRUMENTS
    RELIABLE -->|Yes| BIAS[Identify Biases]
    RELIABLE -->|No| IMPROVE[Improve Reliability]
    
    IMPROVE --> INSTRUMENTS
    
    BIAS --> SELECTION[Selection Bias]
    BIAS --> MEASUREMENT[Measurement Bias]
    BIAS --> CONFOUNDING[Confounding Variables]
    BIAS --> REPORTING[Reporting Bias]
    
    SELECTION --> MITIGATION[Mitigation Strategies]
    MEASUREMENT --> MITIGATION
    CONFOUNDING --> MITIGATION
    REPORTING --> MITIGATION
    
    MITIGATION --> ETHICS{Ethical Review}
    
    ETHICS -->|Required| IRB[IRB Protocols]
    ETHICS -->|Not Required| SKIP_IRB[Skip IRB]
    
    IRB --> CONSENT[Informed Consent]
    IRB --> PRIVACY[Privacy Protection]
    IRB --> RISK[Risk Assessment]
    
    CONSENT --> PROTOCOL[Final Protocol]
    PRIVACY --> PROTOCOL
    RISK --> PROTOCOL
    SKIP_IRB --> PROTOCOL
    
    PROTOCOL --> TIMELINE[Create Timeline]
    TIMELINE --> RESOURCES[Resource Planning]
    RESOURCES --> COMPLETE([Complete])
```

## Synthesis Agent Flow

```mermaid
flowchart TD
    START([Start Synthesis]) --> COLLECT[Collect All Agent Outputs]
    
    COLLECT --> LIT_IN[Literature Review Input]
    COLLECT --> COMP_IN[Comparative Analysis Input]
    COLLECT --> METH_IN[Methodology Input]
    COLLECT --> CIT_IN[Citation Input]
    
    LIT_IN --> NORMALIZE[Normalize Data]
    COMP_IN --> NORMALIZE
    METH_IN --> NORMALIZE
    CIT_IN --> NORMALIZE
    
    NORMALIZE --> STRUCTURE{Identify Structure}
    
    STRUCTURE -->|Hierarchical| HIER[Build Hierarchy]
    STRUCTURE -->|Thematic| THEME[Group by Themes]
    STRUCTURE -->|Chronological| CHRONO[Order by Time]
    STRUCTURE -->|Logical| LOGIC[Logical Flow]
    
    HIER --> INTEGRATE[Integrate Findings]
    THEME --> INTEGRATE
    CHRONO --> INTEGRATE
    LOGIC --> INTEGRATE
    
    INTEGRATE --> CONFLICTS{Check Conflicts}
    
    CONFLICTS -->|Found| RESOLVE[Resolve Conflicts]
    CONFLICTS -->|None| PROCEED_SYNTH[Proceed]
    
    RESOLVE --> PRIORITY{Prioritize by}
    PRIORITY -->|Confidence| CONF_SCORE[Confidence Score]
    PRIORITY -->|Recency| RECENT[Most Recent]
    PRIORITY -->|Authority| AUTH[Source Authority]
    
    CONF_SCORE --> RESOLVED[Conflicts Resolved]
    RECENT --> RESOLVED
    AUTH --> RESOLVED
    
    RESOLVED --> PROCEED_SYNTH
    PROCEED_SYNTH --> NARRATIVE[Build Narrative]
    
    NARRATIVE --> INTRO[Introduction]
    NARRATIVE --> BODY[Main Body]
    NARRATIVE --> CONCLUSION[Conclusions]
    
    INTRO --> CONTEXT_SET[Set Context]
    INTRO --> OBJECTIVES[State Objectives]
    INTRO --> SCOPE[Define Scope]
    
    BODY --> FINDINGS[Present Findings]
    BODY --> EVIDENCE[Support with Evidence]
    BODY --> ANALYSIS[Provide Analysis]
    
    CONCLUSION --> SUMMARY[Summarize Key Points]
    CONCLUSION --> IMPLICATIONS[Discuss Implications]
    CONCLUSION --> FUTURE[Future Directions]
    
    CONTEXT_SET --> COHERENCE{Check Coherence}
    OBJECTIVES --> COHERENCE
    SCOPE --> COHERENCE
    FINDINGS --> COHERENCE
    EVIDENCE --> COHERENCE
    ANALYSIS --> COHERENCE
    SUMMARY --> COHERENCE
    IMPLICATIONS --> COHERENCE
    FUTURE --> COHERENCE
    
    COHERENCE -->|Pass| REFINE[Refine Language]
    COHERENCE -->|Fail| RESTRUCTURE[Restructure]
    
    RESTRUCTURE --> NARRATIVE
    
    REFINE --> STYLE{Apply Style Guide}
    STYLE -->|Academic| ACADEMIC_STYLE[Academic Format]
    STYLE -->|Business| BUSINESS_STYLE[Business Format]
    STYLE -->|Technical| TECHNICAL_STYLE[Technical Format]
    
    ACADEMIC_STYLE --> FINAL[Final Synthesis]
    BUSINESS_STYLE --> FINAL
    TECHNICAL_STYLE --> FINAL
    
    FINAL --> VALIDATE{Validate Completeness}
    VALIDATE -->|Complete| OUTPUT[Generate Output]
    VALIDATE -->|Incomplete| GAPS_FILL[Fill Gaps]
    
    GAPS_FILL --> INTEGRATE
    OUTPUT --> END([Complete])
```

## Citation & Verification Agent Flow

```mermaid
flowchart TD
    START([Start Citation Process]) --> SOURCES[Collect All Sources]
    
    SOURCES --> EXTRACT[Extract Citation Info]
    EXTRACT --> AUTHORS[Author Names]
    EXTRACT --> TITLE[Title]
    EXTRACT --> YEAR[Publication Year]
    EXTRACT --> JOURNAL[Journal/Publisher]
    EXTRACT --> DOI[DOI/ISBN]
    EXTRACT --> URL[URL]
    
    AUTHORS --> VERIFY{Verify Existence}
    TITLE --> VERIFY
    YEAR --> VERIFY
    JOURNAL --> VERIFY
    DOI --> VERIFY
    URL --> VERIFY
    
    VERIFY -->|Valid| CROSSREF[Check CrossRef]
    VERIFY -->|Invalid| SEARCH_CORRECT[Search Corrections]
    
    SEARCH_CORRECT --> FOUND{Found Correct?}
    FOUND -->|Yes| CROSSREF
    FOUND -->|No| FLAG[Flag as Unverified]
    
    CROSSREF --> METADATA[Retrieve Metadata]
    METADATA --> COMPLETE_CHECK{Complete Info?}
    
    COMPLETE_CHECK -->|Yes| FORMAT_STYLE{Citation Style?}
    COMPLETE_CHECK -->|No| SUPPLEMENT[Supplement Data]
    
    SUPPLEMENT --> SCHOLAR_API[Google Scholar API]
    SUPPLEMENT --> PUBMED_API[PubMed API]
    SUPPLEMENT --> MANUAL[Manual Entry]
    
    SCHOLAR_API --> MERGE[Merge Data]
    PUBMED_API --> MERGE
    MANUAL --> MERGE
    MERGE --> FORMAT_STYLE
    
    FORMAT_STYLE -->|APA| APA[Format APA]
    FORMAT_STYLE -->|MLA| MLA[Format MLA]
    FORMAT_STYLE -->|Chicago| CHICAGO[Format Chicago]
    FORMAT_STYLE -->|IEEE| IEEE_FMT[Format IEEE]
    FORMAT_STYLE -->|Harvard| HARVARD[Format Harvard]
    
    APA --> VALIDATE_FMT{Validate Format}
    MLA --> VALIDATE_FMT
    CHICAGO --> VALIDATE_FMT
    IEEE_FMT --> VALIDATE_FMT
    HARVARD --> VALIDATE_FMT
    FLAG --> VALIDATE_FMT
    
    VALIDATE_FMT -->|Valid| CHECK_DUPE{Check Duplicates}
    VALIDATE_FMT -->|Invalid| FIX[Fix Format]
    
    FIX --> FORMAT_STYLE
    
    CHECK_DUPE -->|Found| MERGE_DUPE[Merge Duplicates]
    CHECK_DUPE -->|None| SORT[Sort Citations]
    
    MERGE_DUPE --> SORT
    
    SORT --> ALPHA{Sort Order}
    ALPHA -->|Alphabetical| SORT_ALPHA[Sort by Author]
    ALPHA -->|Chronological| SORT_CHRONO[Sort by Year]
    ALPHA -->|Appearance| SORT_APPEAR[Order of Appearance]
    
    SORT_ALPHA --> NUMBER[Number Citations]
    SORT_CHRONO --> NUMBER
    SORT_APPEAR --> NUMBER
    
    NUMBER --> INLINE{Generate Inline}
    INLINE -->|Yes| IN_TEXT[In-text Citations]
    INLINE -->|No| SKIP_INLINE[Skip Inline]
    
    IN_TEXT --> BIBLIOGRAPHY[Generate Bibliography]
    SKIP_INLINE --> BIBLIOGRAPHY
    
    BIBLIOGRAPHY --> QUALITY{Quality Check}
    QUALITY -->|Pass| OUTPUT[Output Citations]
    QUALITY -->|Fail| REVIEW[Manual Review]
    
    REVIEW --> BIBLIOGRAPHY
    OUTPUT --> END([Complete])
```

## Agent Coordination Flow

```mermaid
flowchart TD
    START([Start Coordination]) --> INIT[Initialize Agents]
    
    INIT --> REGISTER[Register Agents]
    REGISTER --> LIT_REG[Literature Agent]
    REGISTER --> COMP_REG[Comparative Agent]
    REGISTER --> METH_REG[Methodology Agent]
    REGISTER --> SYNTH_REG[Synthesis Agent]
    REGISTER --> CIT_REG[Citation Agent]
    
    LIT_REG --> DEPS{Define Dependencies}
    COMP_REG --> DEPS
    METH_REG --> DEPS
    SYNTH_REG --> DEPS
    CIT_REG --> DEPS
    
    DEPS --> GRAPH[Build Dependency Graph]
    GRAPH --> CYCLES{Check Cycles}
    
    CYCLES -->|Found| ERROR[Report Error]
    CYCLES -->|None| SCHEDULE[Create Schedule]
    
    SCHEDULE --> PARALLEL{Can Parallelize?}
    
    PARALLEL -->|Yes| GROUPS[Group Tasks]
    PARALLEL -->|No| SEQUENTIAL[Sequential Order]
    
    GROUPS --> GROUP1[Group 1: Lit, Meth]
    GROUPS --> GROUP2[Group 2: Comp]
    GROUPS --> GROUP3[Group 3: Synth, Cit]
    
    GROUP1 --> EXECUTE1[Execute Group 1]
    EXECUTE1 --> WAIT1{All Complete?}
    WAIT1 -->|No| MONITOR1[Monitor Progress]
    WAIT1 -->|Yes| GROUP2
    MONITOR1 --> WAIT1
    
    GROUP2 --> EXECUTE2[Execute Group 2]
    EXECUTE2 --> WAIT2{Complete?}
    WAIT2 -->|No| MONITOR2[Monitor Progress]
    WAIT2 -->|Yes| GROUP3
    MONITOR2 --> WAIT2
    
    GROUP3 --> EXECUTE3[Execute Group 3]
    EXECUTE3 --> WAIT3{All Complete?}
    WAIT3 -->|No| MONITOR3[Monitor Progress]
    WAIT3 -->|Yes| AGGREGATE[Aggregate Results]
    MONITOR3 --> WAIT3
    
    SEQUENTIAL --> SEQ_LIT[Execute Literature]
    SEQ_LIT --> SEQ_COMP[Execute Comparative]
    SEQ_COMP --> SEQ_METH[Execute Methodology]
    SEQ_METH --> SEQ_SYNTH[Execute Synthesis]
    SEQ_SYNTH --> SEQ_CIT[Execute Citation]
    SEQ_CIT --> AGGREGATE
    
    AGGREGATE --> VALIDATE{Validate Results}
    VALIDATE -->|Valid| COMPLETE[Mark Complete]
    VALIDATE -->|Invalid| RETRY{Retry Failed}
    
    RETRY -->|Yes| SCHEDULE
    RETRY -->|No| PARTIAL[Return Partial]
    
    COMPLETE --> END([Complete])
    PARTIAL --> END
    ERROR --> END
```

## Conflict Resolution Flow

```mermaid
flowchart TD
    START([Start Conflict Resolution]) --> DETECT[Detect Conflicts]
    
    DETECT --> TYPES{Conflict Type}
    
    TYPES -->|Data| DATA_CONF[Data Conflicts]
    TYPES -->|Semantic| SEM_CONF[Semantic Conflicts]
    TYPES -->|Temporal| TEMP_CONF[Temporal Conflicts]
    TYPES -->|Authority| AUTH_CONF[Authority Conflicts]
    
    DATA_CONF --> NUMERICAL{Numerical Difference}
    NUMERICAL -->|Small| AVERAGE[Take Average]
    NUMERICAL -->|Large| INVESTIGATE[Investigate Source]
    
    SEM_CONF --> MEANING[Analyze Meaning]
    MEANING --> CONTEXT[Check Context]
    CONTEXT --> DISAMBIGUATE[Disambiguate]
    
    TEMP_CONF --> TIMELINE[Check Timeline]
    TIMELINE --> RECENT{Use Recent?}
    RECENT -->|Yes| USE_RECENT[Select Recent]
    RECENT -->|No| USE_RELEVANT[Select Relevant]
    
    AUTH_CONF --> CREDIBILITY[Assess Credibility]
    CREDIBILITY --> SCORE[Calculate Scores]
    SCORE --> HIGHEST[Select Highest]
    
    AVERAGE --> RESOLUTION[Apply Resolution]
    INVESTIGATE --> MANUAL{Manual Review?}
    DISAMBIGUATE --> RESOLUTION
    USE_RECENT --> RESOLUTION
    USE_RELEVANT --> RESOLUTION
    HIGHEST --> RESOLUTION
    
    MANUAL -->|Yes| HUMAN[Human Review]
    MANUAL -->|No| HEURISTIC[Apply Heuristics]
    
    HUMAN --> DECISION[Make Decision]
    HEURISTIC --> AUTO_DECIDE[Auto Decision]
    
    DECISION --> RESOLUTION
    AUTO_DECIDE --> RESOLUTION
    
    RESOLUTION --> DOCUMENT[Document Resolution]
    DOCUMENT --> CONFIDENCE{Confidence Level}
    
    CONFIDENCE -->|High| PROCEED[Proceed]
    CONFIDENCE -->|Medium| FLAG_MEDIUM[Flag for Review]
    CONFIDENCE -->|Low| FLAG_LOW[Flag Critical]
    
    PROCEED --> UPDATE[Update Results]
    FLAG_MEDIUM --> UPDATE
    FLAG_LOW --> UPDATE
    
    UPDATE --> PROPAGATE[Propagate Changes]
    PROPAGATE --> AFFECTED{Check Affected}
    
    AFFECTED -->|Found| REPROCESS[Reprocess Affected]
    AFFECTED -->|None| COMPLETE[Complete]
    
    REPROCESS --> VALIDATE{Validate Changes}
    VALIDATE -->|Valid| COMPLETE
    VALIDATE -->|Invalid| DETECT
    
    COMPLETE --> END([Complete])
```

## Quality Assurance Flow

```mermaid
flowchart TD
    START([Start QA Process]) --> INPUT[Receive Agent Output]
    
    INPUT --> STRUCTURAL{Structural Check}
    
    STRUCTURAL -->|Pass| CONTENT[Content Check]
    STRUCTURAL -->|Fail| FIX_STRUCT[Fix Structure]
    
    FIX_STRUCT --> RETRY_STRUCT{Retry?}
    RETRY_STRUCT -->|Yes| INPUT
    RETRY_STRUCT -->|No| REJECT[Reject Output]
    
    CONTENT --> COMPLETENESS{Completeness Check}
    
    COMPLETENESS -->|Complete| ACCURACY[Accuracy Check]
    COMPLETENESS -->|Incomplete| IDENTIFY_GAPS[Identify Gaps]
    
    IDENTIFY_GAPS --> FILL{Can Fill?}
    FILL -->|Yes| SUPPLEMENT[Supplement Data]
    FILL -->|No| FLAG_INCOMPLETE[Flag Incomplete]
    
    SUPPLEMENT --> ACCURACY
    FLAG_INCOMPLETE --> ACCURACY
    
    ACCURACY --> FACTUAL{Fact Check}
    FACTUAL -->|Verified| CONSISTENCY[Consistency Check]
    FACTUAL -->|Unverified| VERIFY_FACTS[Verify Facts]
    
    VERIFY_FACTS --> SOURCES{Check Sources}
    SOURCES -->|Valid| CONSISTENCY
    SOURCES -->|Invalid| CORRECT[Correct Facts]
    
    CORRECT --> CONSISTENCY
    
    CONSISTENCY --> INTERNAL{Internal Consistency}
    INTERNAL -->|Consistent| EXTERNAL[External Consistency]
    INTERNAL -->|Inconsistent| RESOLVE_INT[Resolve Internal]
    
    RESOLVE_INT --> EXTERNAL
    
    EXTERNAL -->|Consistent| RELEVANCE[Relevance Check]
    EXTERNAL -->|Inconsistent| RESOLVE_EXT[Resolve External]
    
    RESOLVE_EXT --> RELEVANCE
    
    RELEVANCE --> SCORE_REL{Relevance Score}
    SCORE_REL -->|High| CLARITY[Clarity Check]
    SCORE_REL -->|Medium| IMPROVE_REL[Improve Relevance]
    SCORE_REL -->|Low| REJECT
    
    IMPROVE_REL --> CLARITY
    
    CLARITY --> READABILITY{Readability Score}
    READABILITY -->|Good| FINAL_CHECK[Final Check]
    READABILITY -->|Poor| SIMPLIFY[Simplify Language]
    
    SIMPLIFY --> FINAL_CHECK
    
    FINAL_CHECK --> OVERALL{Overall Quality}
    OVERALL -->|Excellent| APPROVE[Approve]
    OVERALL -->|Good| APPROVE_NOTES[Approve with Notes]
    OVERALL -->|Poor| REWORK[Request Rework]
    
    APPROVE --> STAMP[Quality Stamp]
    APPROVE_NOTES --> STAMP
    REWORK --> INPUT
    REJECT --> LOG[Log Issues]
    
    STAMP --> OUTPUT[Output Result]
    LOG --> OUTPUT
    
    OUTPUT --> END([Complete])
```