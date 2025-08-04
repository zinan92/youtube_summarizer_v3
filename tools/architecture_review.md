# Architectural Consistency Checker

## Purpose
Identify inconsistencies in how different parts of your system solve similar problems. These inconsistencies often reveal unnecessary complexity or hidden assumptions.

## How to Use This Template

### 1. Identify Similar Operations
List operations in your system that should logically work the same way:

```
Similar Operations Audit:

Operation Type: File Processing
- Audio download: ________________
- Audio transcription: ____________  
- Text processing: _______________
- File saving: __________________

Operation Type: Error Handling  
- Network failures: ______________
- File system errors: ____________
- API timeouts: _________________
- Processing errors: _____________

Operation Type: Async Operations
- Database queries: ______________
- API calls: ____________________
- File I/O: _____________________
- Background tasks: ______________
```

### 2. Pattern Comparison Matrix

For each group of similar operations, fill out:

| Operation | Pattern Used | Success Rate | Complexity | Consistent? |
|-----------|-------------|--------------|------------|-------------|
| Audio download | Completion hooks | 99% | Low | ✅ |
| Audio transcription | Timeout + signals | 60% | High | ❌ |
| Text processing | Simple API call | 95% | Low | ❌ |

**Red flags**: Different patterns, low success rates, high complexity

### 3. Inconsistency Investigation Template

For each inconsistency found:

```
## Inconsistency Report

**Operations**: Audio download vs. Audio transcription
**Pattern A**: Completion hooks (download)
**Pattern B**: Timeout + signals (transcription)

**Why the difference?**
- Historical reasons: _____________
- Technical constraints: __________
- Different requirements: ________
- No good reason: _______________

**Which pattern is better?**
- Pattern A wins because: ________
- Pattern B wins because: ________
- Neither, need new approach: ____

**Action Plan**:
- [ ] Standardize on Pattern A
- [ ] Standardize on Pattern B  
- [ ] Create new consistent pattern
- [ ] Document why difference is necessary

**Expected Impact**:
- Reduced complexity: ___________
- Improved reliability: __________
- Better maintainability: ________
```

## Common Architecture Anti-Patterns

Watch for these consistency problems:

### 1. Mixed Async Patterns
```
❌ BAD:
- Some operations use callbacks
- Some use promises  
- Some use async/await
- Some use threading

✅ GOOD:
- Consistent async pattern throughout
- Clear guidelines for when to use each
```

### 2. Inconsistent Error Handling
```
❌ BAD:
- Some functions throw exceptions
- Some return error codes
- Some use global error handlers
- Some fail silently

✅ GOOD:  
- Consistent error handling strategy
- Clear error propagation rules
```

### 3. Mixed Completion Patterns
```
❌ BAD (Our original problem):
- Downloads wait for completion signals
- Transcription uses arbitrary timeouts
- File saves assume immediate completion

✅ GOOD (Our solution):
- All operations wait for natural completion
- Timeouts only for network operations
- Consistent completion handling
```

### 4. Inconsistent State Management
```
❌ BAD:
- Some state in global variables
- Some state in objects
- Some state in databases
- Some state in files

✅ GOOD:
- Clear state management strategy
- Consistent state location rules
```

## Architecture Review Process

### Monthly Architecture Review

1. **Collect similar operations** (30 minutes)
   - List all operations of similar types
   - Group them by function (I/O, processing, error handling, etc.)

2. **Pattern analysis** (45 minutes)
   - Identify what pattern each operation uses
   - Note success rates and complexity
   - Flag inconsistencies

3. **Investigation** (60 minutes)
   - For each inconsistency, determine the root cause
   - Decide which pattern should be standard
   - Create action items for standardization

4. **Documentation** (15 minutes)
   - Update architecture guidelines
   - Document decisions and rationale
   - Schedule follow-up review

### Pre-Code Review Checklist

Before submitting code, ask:
- [ ] Does this use the same pattern as similar operations?
- [ ] If using a different pattern, is there a documented reason?
- [ ] Does this introduce a new way of doing something we already do?
- [ ] Should existing code be updated to match this pattern?

## Real-World Examples

### Example 1: Our Timeout Issue
```
Inconsistency: Download vs. Transcription completion handling
Root cause: Inherited timeout code without questioning
Solution: Standardize on completion signals
Result: 100% success rate, reduced complexity
```

### Example 2: Database Access Patterns
```
Inconsistency: Some queries use ORM, some use raw SQL
Root cause: Different developers, different preferences  
Solution: Standardize on ORM for simple queries, raw SQL for complex
Result: Clearer code, easier maintenance
```

### Example 3: Configuration Management
```
Inconsistency: Some config in env vars, some in files, some hardcoded
Root cause: Organic growth, no planning
Solution: All config in .env files, with documented precedence rules
Result: Easier deployment, better security
```

## Automated Consistency Checking

### Simple Pattern Detection Script
```python
# Example: Find functions that handle similar operations differently
import ast
import os

def find_timeout_patterns(directory):
    """Find different timeout handling patterns in codebase"""
    patterns = {}
    
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                # Analyze timeout handling patterns
                # Flag inconsistencies
                pass
    
    return patterns
```

### Code Pattern Analysis
- Use static analysis tools to identify similar functions
- Flag functions that solve similar problems differently
- Generate reports of potential inconsistencies

## Success Metrics

Track these metrics to measure architectural consistency:

- **Pattern consistency rate**: % of similar operations using same pattern
- **Bug correlation**: Do inconsistent areas have more bugs?
- **Development speed**: Does consistency improve feature velocity?
- **Maintenance time**: Does consistency reduce maintenance overhead?

## Integration with Development Process

### Architecture Decision Records (ADRs)
Document why you chose specific patterns:

```
## ADR-001: Completion Handling Pattern

**Status**: Accepted  
**Date**: 2024-01-XX

**Context**: 
We need consistent way to handle operation completion

**Decision**: 
Use completion signals, not timeouts, for local operations

**Consequences**:
- More reliable completion detection
- Consistent pattern across all operations  
- Easier to debug and maintain

**Alternatives Considered**:
- Timeout-based completion (rejected: unreliable)
- Polling-based completion (rejected: inefficient)
```

Remember: Consistency isn't about rigidity—it's about reducing cognitive load and preventing bugs through predictable patterns.