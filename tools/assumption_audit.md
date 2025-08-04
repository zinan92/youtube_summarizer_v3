# Assumption Audit Toolkit

## Quick Checklist for Any Persistent Problem

### 1. The Five Whys (Enhanced)
- Why does this problem exist?
- Why does the problematic component exist?
- Why was it built this way originally?
- Why haven't we changed it?
- Why are we accepting this constraint?

### 2. Assumption Inventory Template

For each major component, complete:

| Component | "This exists because..." | Is this still valid? | What if we removed it? |
|-----------|--------------------------|---------------------|------------------------|
| Timeout mechanism | "Whisper might hang" | ❌ Whisper doesn't hang | ✅ Works perfectly |
| Retry logic | "API calls might fail" | ✅ Network calls do fail | ❌ Still needed |
| Chunking system | "Large files cause issues" | ✅ Memory/size limits | ❌ Still needed |

### 3. Architectural Consistency Audit

Compare how your system handles similar operations:

```
Operation A: ________________
Pattern: ___________________
Success rate: ______________

Operation B: ________________  
Pattern: ___________________
Success rate: ______________

Inconsistency detected? ______
Should B work like A? _______
```

### 4. Remove-First Debugging Protocol

Before adding complexity:

1. **Identify the problematic component**
   - [ ] What specifically is causing the issue?
   
2. **Question its necessity**
   - [ ] What would happen if we deleted this entirely?
   - [ ] Can we test this hypothesis safely?
   
3. **Try removal first**
   - [ ] Comment out the problematic code
   - [ ] Run tests to see what breaks
   - [ ] Often nothing breaks (like our timeout case)

4. **If removal works, stop there**
   - [ ] Don't add back complexity
   - [ ] Document why the removal worked

### 5. Beginner's Mind Question Bank

Use these questions to challenge assumptions:

**For any component:**
- Why does this exist?
- What problem does this solve?
- How do other systems solve this problem?
- What would confuse a new developer about this?

**For any pattern:**
- Why do we do it this way?
- What would happen if we did the opposite?
- Is this pattern consistent across the system?
- What assumptions does this pattern make?

**For any constraint:**
- Who decided this was necessary?
- When was this decision made?
- Has the context changed since then?
- What would happen if we ignored this constraint?

## Real-World Application Examples

### Example 1: Timeout Investigation (Our Case)
```
Component: Transcription timeout mechanism
"This exists because...": "Whisper might hang indefinitely"
Is this still valid?: NO - Whisper is a deterministic local computation
What if we removed it?: Transcription works perfectly
Action: Remove all timeout mechanisms
Result: Problem solved
```

### Example 2: Database Connection Pooling
```
Component: Custom connection pooling
"This exists because...": "Database connections are expensive"
Is this still valid?: Maybe - check if modern libs handle this
What if we removed it?: Use library's built-in pooling
Action: Test with library defaults first
Result: Often simpler and more reliable
```

## Integration with Development Workflow

### Code Review Checklist
Add these questions to your standard code review:
- [ ] Does this add complexity or remove it?
- [ ] Is this pattern consistent with how we solve similar problems?
- [ ] What assumptions does this code make?
- [ ] Could we solve this by removing something instead?

### Retrospective Questions
Ask these during project retrospectives:
- What problems did we overcomplicate?
- What constraints did we accept without questioning?
- Where did we add complexity instead of removing root causes?
- What assumptions turned out to be wrong?

### New Project Setup
Start every project with:
- [ ] Document the core assumptions
- [ ] Identify what constraints are real vs. inherited
- [ ] List what patterns we'll use consistently
- [ ] Plan regular assumption review sessions

## Warning Signs of Hidden Assumptions

Watch for these patterns in your codebase:

🚩 **Different solutions for similar problems**
- Download uses completion hooks, transcription uses timeouts

🚩 **Complex solutions for simple problems**  
- 500 lines of retry logic for a local library call

🚩 **Cargo cult programming**
- "We do this because the old system did this"

🚩 **Defensive programming gone wrong**
- Adding timeouts "just in case" without understanding the failure modes

🚩 **Inconsistent error handling**
- Some operations fail fast, others retry indefinitely

## Success Stories Template

Document wins from assumption questioning:

```
## [Date] - [Problem Title]

**Original Problem**: 
**Failed Solutions Tried**: 
**Breakthrough Question**: 
**Root Assumption**: 
**Solution**: 
**Result**: 
**Time Saved**: 
**Lessons Learned**: 
```

Keep a log of these to build institutional memory and celebrate the power of simple questions.